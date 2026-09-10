"""Replay OSM translation fitting against immutable, offline delivery vectors.

Example (run once per implementation, in separate Python processes)::

    python tools/benchmark_osm_matching.py --implementation-root . \
        --baseline-root baselines --report validation/osm-benchmark.json

The delivered CABLE layer is an input specimen, never geolocation ground truth.
An optional --perturb-m DX DY measures fit equivariance to a known displacement;
it does not measure accuracy against surveyed checkpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
import time

from pyproj import CRS, Transformer
from shapely import from_wkb


CASES = ("kletek", "taipa", "tinggede", "darat_sekip_sf")


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def value_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def _quote(value):
    return '"' + value.replace('"', '""') + '"'


def decode_geometry(blob, srs_id):
    """Decode the standard GeoPackage binary header, checking its CRS binding."""
    if not isinstance(blob, bytes) or len(blob) < 9 or blob[:3] != b"GP\x00":
        raise ValueError("Expected standard GeoPackage geometry version 0")
    flags = blob[3]
    envelope_code = (flags >> 1) & 7
    if flags & 0xF0 or envelope_code > 4:
        raise ValueError("Empty, extended or reserved GeoPackage geometry is unsupported")
    if struct.unpack("<i" if flags & 1 else ">i", blob[4:8])[0] != srs_id:
        raise ValueError("Geometry SRS does not match its layer")
    offset = 8 + (0, 32, 48, 48, 64)[envelope_code]
    if len(blob) <= offset:
        raise ValueError("Truncated GeoPackage geometry")
    geometry = from_wkb(blob[offset:])
    if geometry.is_empty or geometry.geom_type not in {"LineString", "MultiLineString"}:
        raise ValueError("Benchmark layers require nonempty line geometries")
    return geometry


def read_lines(path, layer):
    """Read ordered XY line parts without loading GDAL or modifying the GPKG."""
    path = Path(path).resolve()
    initial_hash = file_hash(path)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        metadata = connection.execute(
            "SELECT column_name,srs_id FROM gpkg_geometry_columns WHERE table_name=?", (layer,),
        ).fetchone()
        if metadata is None:
            raise ValueError(f"Missing line layer {layer!r} in {path}")
        geometry_column, srs_id = metadata
        spatial_reference = connection.execute(
            "SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id=?", (srs_id,),
        ).fetchone()
        if spatial_reference is None or spatial_reference[0] in {"undefined", ""}:
            raise ValueError(f"Layer {layer!r} has no defined CRS")
        crs = CRS.from_user_input(spatial_reference[0])
        projection = Transformer.from_crs(crs, "EPSG:3857", always_xy=True)
        schema = connection.execute(f"PRAGMA table_info({_quote(layer)})").fetchall()
        keys = [row[1] for row in schema if row[5]]
        if len(keys) != 1:
            raise ValueError("Benchmark requires one stable feature primary key")
        features = []
        for row in connection.execute(f"SELECT * FROM {_quote(layer)} ORDER BY {_quote(keys[0])}"):
            geometry = decode_geometry(row[geometry_column], srs_id)
            parts = list(geometry.geoms) if geometry.geom_type == "MultiLineString" else [geometry]
            for part_index, part in enumerate(parts):
                points = [list(projection.transform(point[0], point[1], errcheck=True)) for point in part.coords]
                if not all(math.isfinite(v) for point in points for v in point):
                    raise ValueError("Projected line contains a nonfinite coordinate")
                if not any(math.dist(a, b) > 1e-8 for a, b in zip(points, points[1:])):
                    raise ValueError("Benchmark does not silently drop zero-length lines")
                attributes = {name: row[name] for name in row.keys() if name != geometry_column}
                features.append({"feature_id": row[keys[0]], "part_index": part_index,
                                 "points": points, "attributes": attributes})
        if not features:
            raise ValueError(f"Layer {layer!r} is empty")
    finally:
        connection.close()
    if file_hash(path) != initial_hash:
        raise ValueError(f"Input changed during read: {path}")
    return features, {"path": str(path), "sha256": initial_hash, "layer": layer,
                      "declared_srs_id": srs_id, "crs_wkt": crs.to_wkt(),
                      "projection_definition": projection.definition,
                      "projection_accuracy_m": projection.accuracy,
                      "line_part_count": len(features), "coordinates": "XY projected to EPSG:3857"}


def _road_vectors(features):
    roads, used = [], set()
    for index, feature in enumerate(features):
        original_id = feature["attributes"].get("osm_id")
        try:
            candidate_id = int(original_id)
        except (TypeError, ValueError):
            candidate_id = -(index + 1)
        if candidate_id in used:
            candidate_id = -(index + 1)
        while candidate_id in used:
            candidate_id -= len(features)
        used.add(candidate_id)
        roads.append({"osm_way_id": candidate_id, "points": feature["points"],
                      "name": feature["attributes"].get("name", ""),
                      "source_osm_id": original_id, "source_feature_id": feature["feature_id"],
                      "source_part_index": feature["part_index"]})
    return roads


def benchmark_case(matching, directory, perturb_m=None):
    directory = Path(directory)
    cable, delivery_input = read_lines(directory / "delivery.gpkg", "CABLE")
    reference, road_input = read_lines(directory / "osm-basemap.gpkg", "roads")
    routes, roads = [feature["points"] for feature in cable], _road_vectors(reference)
    points = [point for route in routes for point in route]
    centre = [(min(p[i] for p in points) + max(p[i] for p in points)) / 2 for i in (0, 1)]
    _, latitude = Transformer.from_crs(3857, 4326, always_xy=True).transform(*centre)
    scale = math.cos(math.radians(latitude))
    if scale <= 0.01:
        raise ValueError("Benchmark requires a usable local Web Mercator scale")
    frozen_hash = value_hash([routes, roads])

    def fit(values):
        start = time.perf_counter()
        delta, quality = matching._match_routes(values, roads, scale, [0.0, 0.0])
        return {"accepted": delta is not None, "translation_3857": delta,
                "elapsed_seconds": time.perf_counter() - start, "quality": quality}

    result = {"inputs": {"delivery": delivery_input, "roads": road_input},
              "input_role": "archived_delivery_specimen_not_geolocation_ground_truth",
              "reference_role": "offline_osm_geometry_only_no_node_topology",
              "authority": "relative_osm", "absolute_accuracy_verified": False,
              "projected_vectors_sha256": frozen_hash,
              "settings": {"initial_translation_3857": [0.0, 0.0], "reference_latitude": latitude,
                           "scale_to_ground": scale, "distance_method": "reference_latitude_cosine_approximation",
                           "projection_network": "OFF"},
              "unperturbed": fit(routes)}
    if perturb_m is not None:
        shift = [float(v) / scale for v in perturb_m]
        shifted = [[[x + shift[0], y + shift[1]] for x, y in route] for route in routes]
        perturbed = fit(shifted)
        base_delta = result["unperturbed"]["translation_3857"]
        shifted_delta = perturbed["translation_3857"]
        accepted_both = base_delta is not None and shifted_delta is not None
        error = math.dist(base_delta, [shifted_delta[i] + shift[i] for i in (0, 1)]) * scale if accepted_both else None
        result["perturbation"] = {"requested_offset_m_approx": list(perturb_m), "applied_offset_3857": shift,
                                  "expected_translation_increment_3857": [-v for v in shift],
                                  "fit": perturbed, "both_fits_accepted": accepted_both,
                                  "accepted_solution_equivariance_error_m_approx": error,
                                  "interpretation": "fit_stability_only_not_error_against_ground_truth"}
    if value_hash([routes, roads]) != frozen_hash:
        raise ValueError("Matcher mutated benchmark input vectors")
    for item in (delivery_input, road_input):
        if file_hash(item["path"]) != item["sha256"]:
            raise ValueError("Benchmark input file changed during fitting")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--implementation-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--perturb-m", nargs=2, type=float, metavar=("DX", "DY"))
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error("Refusing to overwrite an existing benchmark report")
    if args.perturb_m and not all(math.isfinite(v) for v in args.perturb_m):
        parser.error("Perturbation must be finite")
    os.environ["PROJ_NETWORK"] = "OFF"
    from pyproj import network
    network.set_network_enabled(False)
    source_root = (args.implementation_root.resolve() / "src").resolve()
    sys.path.insert(0, str(source_root))
    matching = importlib.import_module("cad2gis.cad2gis_v3.osm_matching")
    if not Path(matching.__file__).resolve().is_relative_to(source_root):
        raise ValueError("Selected implementation was already shadowed by another import")
    module_paths = [Path(module.__file__).resolve() for module in (
        matching, matching.osm_anchor, importlib.import_module("cad2gis.cad2gis_v3.osm_evidence"),
    )]
    implementation_hashes = {str(path): file_hash(path) for path in module_paths}
    import numpy
    import pyproj
    import shapely
    result = {"schema": "cad2gis.offline_osm_benchmark.v1", "absolute_accuracy_verified": False,
              "limitation": "Archived delivery coordinates and OSM vectors are not surveyed ground truth. "
                            "Acceptance and residuals describe relative alignment only; no automatic accuracy claim.",
              "implementation_root": str(args.implementation_root.resolve()),
              "implementation_sha256": implementation_hashes,
              "benchmark_script_sha256": file_hash(__file__),
              "runtime": {"python": sys.version, "numpy": numpy.__version__, "pyproj": pyproj.__version__,
                          "shapely": shapely.__version__}, "cases": {}}
    for case in args.cases:
        print(f"Benchmarking {case}", file=sys.stderr, flush=True)
        result["cases"][case] = benchmark_case(matching, args.baseline_root / case / "delivery", args.perturb_m)
    if implementation_hashes != {str(path): file_hash(path) for path in module_paths}:
        raise ValueError("Implementation changed during benchmark; rerun for reproducible results")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(str(args.report.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
