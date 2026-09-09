"""Run the real converter on an explicitly reviewed, generated test drawing.

This fixture is never applied to a user's drawing. Source binding is derived by
the installed reader and the assertions describe geometry authored below.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess


def run(bundle: Path, output: Path) -> None:
    import ezdxf
    from cad2gis import pipeline
    from pyproj import Transformer

    output.mkdir(parents=True, exist_ok=False)
    native = bundle / "libredwg" / ("" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    os.environ["CAD2GIS_LIBREDWG_CLI"] = str(native / ("dwg2dxf" + suffix))
    document = ezdxf.new("R2000")
    document.header["$INSUNITS"] = 6
    document.layers.new("TEST_ROUTE")
    document.layers.new("TEST_POLE")
    block = document.blocks.new("TEST_POLE")
    block.add_circle((0, 0), 1)
    origin = (12600000.0, -800000.0)
    x, y = origin
    routes = [[(x, y), (x + 100, y), (x + 140, y + 40)],
              [(x + 100, y), (x + 100, y - 80), (x + 170, y - 120)]]
    for route in routes:
        document.modelspace().add_lwpolyline(route, dxfattribs={"layer": "TEST_ROUTE"})
    document.modelspace().add_blockref("TEST_POLE", origin, dxfattribs={"layer": "TEST_POLE"})
    dxf, dwg = output / "generated.dxf", output / "generated.dwg"
    document.saveas(dxf)
    subprocess.run([str(native / ("dxf2dwg" + suffix)), "--as", "r2000", "-o", str(dwg), str(dxf)],
                   check=True, capture_output=True, timeout=60)
    project = output / "project"
    pipeline.bootstrap_project(source=dwg, project_dir=project)
    profile_path, mapping_path = project / "config/source_profile.json", project / "config/mapping_registry.json"
    profile, mapping = [json.loads(path.read_text(encoding="utf-8")) for path in (profile_path, mapping_path)]
    review = {"status": "reviewed", "reviewed_by": "synthetic-installation-fixture",
              "reviewed_at": datetime.now(timezone.utc).isoformat(),
              "provenance": "tools/smoke_synthetic_conversion.py authors two metre-coordinate routes and one pole; test data only"}
    profile["review"] = dict(review)
    profile["crs"].update(source_crs="EPSG:3857", target_crs="EPSG:3857")
    profile["drawing"].update(drawing_units="metre", source_coordinate_scale_to_m=1.0,
                              source_coordinate_scale_reviewed=True)
    profile["expectations"]["feature_counts"] = {"CABLE": 2, "INFRASTRUCTURE": 2, "PTECH": 1}
    mapping["review"] = dict(review)
    mapping["block_families"] = {"PTECH": ["TEST_POLE"]}
    mapping["positive_route_layer_regex"] = "^TEST_ROUTE$"
    # The circle is authored as symbol evidence inside the sole pole block.
    mapping["coverage"]["semantics"]["allowlist"] = ["materialized_block_geometry_evidence_only"]
    mapping["thresholds_native_m"] = {"exact": 1e-6, "route_to_asset": 1.0,
        "dimension_to_support": 2.0, "device_to_support_candidate": 1.0, "device_collocation_to_support_m": 1.0}
    for path, payload in ((profile_path, profile), (mapping_path, mapping)):
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    geographic = Transformer.from_crs(3857, 4326, always_xy=True)
    osm = {"elements": [{"type": "way", "id": index + 1, "tags": {"highway": "residential"},
        "geometry": [{"lon": lon, "lat": lat} for lon, lat in
                     [geographic.transform(px + 100, py + 200) for px, py in route]]}
        for index, route in enumerate(routes)]}
    osm_path = output / "osm-fixture.json"
    osm_path.write_text(json.dumps(osm), encoding="utf-8")
    result = pipeline.convert_project(source=dwg, project_dir=project, run_dir=output / "run", osm_data=osm_path)
    manifest = json.loads(result.run_manifest_path.read_text(encoding="utf-8"))
    assert result.delivery_path.is_file()
    assert manifest["osm_matching"]["status"] == "matched", manifest["osm_matching"]
    assert manifest["svg_candidates"]["status"] == "completed", manifest["svg_candidates"]
    from cad2gis.native_runtime import ensure_osgeo_runtime
    ensure_osgeo_runtime()
    from osgeo import ogr
    data = ogr.Open(str(result.delivery_path))
    layer = data.GetLayerByName("PTECH")
    feature = layer.GetNextFeature()
    actual = feature.GetGeometryRef().GetPoint_2D(0)
    expected = (origin[0] + 100, origin[1] + 200)
    assert all(abs(a - b) < 0.2 for a, b in zip(actual, expected)), (actual, expected)
    data = None
    report = {"schema": "cad2gis.synthetic_conversion_smoke.v1", "status": "passed", "source": str(dwg),
              "project": str(project), "run": str(result.run_manifest_path.parent),
              "osm_status": manifest["osm_matching"]["status"], "svg_status": manifest["svg_candidates"]["status"],
              "matched_point": actual, "expected_point": expected, "coordinate_tolerance_m": 0.2}
    (output / "conversion-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.bundle.resolve(), args.output.resolve())
