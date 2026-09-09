"""Exercise an installed bundle with its own Python, native libraries and MCP.

Run with the installed Python (-I). Test files are synthetic and remain in the
explicit output directory; no private project or network service is required.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


async def probe_mcp(command: Path, project: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "CAD2GIS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR"):
        env.pop(key, None)
    env["CAD2GIS_PROJECT_ROOTS"] = str(project)
    async with stdio_client(StdioServerParameters(command=str(command), args=["--transport", "stdio"], env=env)) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            initialization = await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            for required in ("inspect_source", "run_conversion", "get_capabilities"):
                assert required in names, required
            capabilities = await session.call_tool("get_capabilities", {})
            assert not capabilities.isError
            denied = await session.call_tool("inspect_source", {"source": str(project.parent / "outside.dwg")})
            assert denied.isError, "MCP allowed a source outside its explicit project root"
            return {"server": initialization.serverInfo.model_dump(), "tool_count": len(names),
                    "capabilities": "passed", "outside_project_denied": True}


def run(bundle: Path, output: Path) -> dict:
    import cad2gis
    import ezdxf
    from cad2gis.native_runtime import ensure_osgeo_runtime
    from cad2gis.pipeline import export_source
    from cad2gis.symbol_workflow import prepare
    from pyproj import Transformer
    from shapely.geometry import Point

    bundle = bundle.resolve(strict=True)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    assert Path(cad2gis.__file__).resolve().is_relative_to(bundle), cad2gis.__file__
    suffix = ".exe" if os.name == "nt" else ""
    bin_dir = bundle / "bin"
    native_dir = bundle / "libredwg" / ("" if os.name == "nt" else "bin")
    os.environ["CAD2GIS_LIBREDWG_CLI"] = str(native_dir / ("dwg2dxf" + suffix))
    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    os.environ.pop("CAD2GIS_BACKEND_PATH", None)
    doctor = subprocess.run([str(bin_dir / ("cad2gis" + suffix)), "doctor", "--deep", "--strict", "--profile", "full", "--json"],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    (output / "doctor.json").write_text(doctor.stdout, encoding="utf-8")
    (output / "doctor.stderr.txt").write_text(doctor.stderr, encoding="utf-8")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert ensure_osgeo_runtime()["available"]
    from osgeo import ogr, osr

    document = ezdxf.new("R2000")
    document.header["$INSUNITS"] = 6
    block = document.blocks.new("SMOKE_POLE")
    block.add_circle((0, 0), 1)
    model = document.modelspace()
    model.add_blockref("SMOKE_POLE", (500000, 1000000))
    model.add_line((500000, 1000000), (500100, 1000010))
    dxf = output / "Synthetic drawing 空格.dxf"
    dwg = output / "Synthetic drawing 空格.dwg"
    document.saveas(dxf)
    encoded = subprocess.run([str(native_dir / ("dxf2dwg" + suffix)), "--as", "r2000", "-o", str(dwg), str(dxf)],
                             capture_output=True, timeout=60)
    assert encoded.returncode == 0 and dwg.is_file(), encoded.stderr
    snapshot = export_source(source=dwg, run_dir=output / "source-export")
    # Read the actual returned DWG to bind the glyph to the reader's insert handle.
    from cad2gis.reader.resolver import extract_records
    records = list(extract_records(dwg))
    insert = next(record for record in records if record.get("block_name") == "SMOKE_POLE" and record.get("layout") == "Model")
    database = output / "synthetic.gpkg"
    driver = ogr.GetDriverByName("GPKG")
    data = driver.CreateDataSource(str(database))
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(3857)
    layer = data.CreateLayer("PTECH", srs=crs, geom_type=ogr.wkbPoint)
    layer.CreateField(ogr.FieldDefn("source_handle", ogr.OFTString))
    row = ogr.Feature(layer.GetLayerDefn())
    row.SetField("source_handle", str(insert["handle"]))
    row.SetGeometry(ogr.CreateGeometryFromWkt("POINT (500000 1000000)"))
    assert layer.CreateFeature(row) == 0
    row = layer = data = None
    symbols = prepare(dwg, [database], output / "svg-candidates")
    assert symbols["all_points_accounted_for"] == 1
    assert list((output / "svg-candidates").rglob("*.svg")), "No native SVG was generated"
    point = Transformer.from_crs(3857, 4326, always_xy=True).transform(500000, 1000000)
    assert Point(point).is_valid and 4 < point[0] < 5 and 8 < point[1] < 10
    mcp = asyncio.run(probe_mcp(bin_dir / ("cad2gis-agent-mcp" + suffix), output))
    report = {"schema": "cad2gis.bundle_smoke.v1", "bundle": str(bundle), "python": sys.executable,
              "imported_package": cad2gis.__file__, "doctor": "passed", "native_dwg_roundtrip": "passed",
              "source_export": snapshot, "gdal_geopackage": "passed", "proj_geos": "passed",
              "svg_candidate_count": 1, "source_sha256": hashlib.sha256(dwg.read_bytes()).hexdigest(), "mcp": mcp}
    (output / "smoke-report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.bundle, args.output), indent=2, default=str))


if __name__ == "__main__":
    main()
