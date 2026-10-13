"""Run the real optional CAD sidecar and canonical GIS pipeline on synthetic CAD."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def verify_bundle(workdir: Path, output: Path) -> dict:
    import ezdxf
    from cad2gis.cad_bundle_workspace import create_workspace, sha256
    from cad2gis.cad_bundles import discover_bundle, invoke_bundle
    from cad2gis.pipeline import bootstrap_project, convert_project
    workdir = workdir.resolve()
    workdir.mkdir(parents=True)
    source = workdir / "synthetic.dxf"
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    origin = (12_600_000, -800_000)
    doc.modelspace().add_line(origin, (origin[0] + 100, origin[1]), dxfattribs={"layer": "CABLE"})
    doc.modelspace().add_lwpolyline([(origin[0]-10, origin[1]-10),
        (origin[0]+200, origin[1]-10), (origin[0]+200, origin[1]+150),
        (origin[0]-10, origin[1]+150)], close=True, dxfattribs={"layer": "FRAME"})
    doc.modelspace().add_text("SYNTHETIC CAD2GIS TEST", dxfattribs={
        "layer": "NOTES", "insert": (origin[0], origin[1]-20), "height": 2})
    pole = doc.blocks.new("SYNTHETIC_POLE")
    pole.add_circle((0, 0), 1)
    for point in (origin, (origin[0]+100, origin[1]), (origin[0]+100, origin[1]+50)):
        doc.modelspace().add_blockref("SYNTHETIC_POLE", point, dxfattribs={"layer": "POLE"})
    doc.saveas(source)
    original_hash = sha256(source)
    workspace = workdir / "workspace"
    create_workspace(source, workspace)
    async def edit():
        tools = await discover_bundle(workspace)
        layers = await invoke_bundle(workspace, "layer_list", {})
        changed = await invoke_bundle(workspace, "entity_create_line", {
            "x1": origin[0]+100, "y1": origin[1], "x2": origin[0]+100,
            "y2": origin[1]+50, "layer": "CABLE"})
        diff = await invoke_bundle(workspace, "drawing_diff", {})
        topology = await invoke_bundle(workspace, "drawing_topology_check", {"layers": ["CABLE"]})
        (workdir / "sidecar-evidence.json").write_text(json.dumps({
            "tools": tools, "layers": layers, "edit": changed,
            "diff": diff, "topology": topology}, ensure_ascii=False, indent=2))
        return tools, changed
    tools, changed = asyncio.run(edit())
    edited = Path(changed["edited_source"])
    project = workdir / "project"
    bootstrap_project(source=edited, project_dir=project)
    # These declarations describe only the authored fixture, never customer drawings.
    review = {"status": "reviewed", "reviewed_by": "synthetic-fixture-author",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "Authored synthetic geometry, exact CABLE layers, meter units and nominal EPSG:3857; no surveyed control points."}
    profile_path = project / "config/source_profile.json"
    profile = json.loads(profile_path.read_text())
    profile["review"] = review
    profile["drawing"].update(drawing_units="m", source_coordinate_scale_to_m=1.0,
                              source_coordinate_scale_reviewed=True)
    profile["crs"].update(source_crs="EPSG:3857", target_crs="EPSG:3857")
    profile["expectations"]["feature_counts"] = {"CABLE": 2, "PTECH": 3, "INFRASTRUCTURE": 2}
    profile_path.write_text(json.dumps(profile, indent=2))
    mapping_path = project / "config/mapping_registry.json"
    mapping = json.loads(mapping_path.read_text())
    mapping.update(review=review, positive_route_layer_regex="^CABLE$",
                   block_families={"PTECH": ["SYNTHETIC_POLE"]},
                   thresholds_native_m={"exact": 1e-6, "route_to_asset": 1.0,
                       "dimension_to_support": 2.0, "device_to_support_candidate": 1.0,
                       "device_collocation_to_support_m": 1.0},
                   coverage={"semantics": {"policy": "abstain", "allowlist": []},
                             "styles": {"policy": "fail", "allowlist": []}})
    mapping_path.write_text(json.dumps(mapping, indent=2))
    conversion = convert_project(source=edited, project_dir=project,
        run_dir=workdir / "gis-run", matching="nominal", svg_mode="off",
        geometry_repairs="candidate-only")
    manifest = json.loads(conversion.run_manifest_path.read_text())
    report = {"schema_version": "cad2gis.cad_bundle_verification.v1",
        "source_preserved": sha256(source) == original_hash,
        "source_sha256": original_hash, "edited_sha256": sha256(edited),
        "tool_count": len(tools), "source_entity_count": len(ezdxf.readfile(source).modelspace()),
        "edited_entity_count": len(ezdxf.readfile(edited).modelspace()),
        "conversion_source_sha256": manifest["source"]["sha256"],
        "delivery_exists": conversion.delivery_path.is_file(),
        "delivery": str(conversion.delivery_path), "run_status": str(conversion.run_status)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_bundle(args.workdir, args.output), indent=2))
