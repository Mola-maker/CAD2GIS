"""Read protected QGZ specimens at test time; never store duplicate CAD vectors."""

from collections import defaultdict
import json
import math
from pathlib import Path
import sqlite3
import zipfile

from pyproj import CRS, Transformer

from tools.benchmark_osm_matching import decode_geometry


ROOT = Path(__file__).resolve().parents[1]


def baseline_nominal_routes(name):
    """Undo the recorded translation after joining delivered source segments.

    Only the protected baseline is an input. Route identity, segment order,
    declared CRS and the source-bound match profile determine the reconstruction.
    """
    directory = ROOT / "baselines" / name
    profiles = [path for path in (directory / "config").glob("matching-*.json")
                if not path.name.endswith(".evidence.json")]
    assert len(profiles) == 1, "A single selected baseline registration is required"
    match = json.loads(profiles[0].read_text(encoding="utf-8"))
    with zipfile.ZipFile(directory / "delivery/view-with-source-SVG.qgz") as archive:
        names = [name for name in archive.namelist() if name.endswith("_delivery.gpkg")]
        assert len(names) == 1
        database = archive.read(names[0])
    routes = defaultdict(list)
    with sqlite3.connect(":memory:") as connection:
        connection.deserialize(database)
        connection.row_factory = sqlite3.Row
        column, srs = connection.execute(
            "SELECT column_name,srs_id FROM gpkg_geometry_columns WHERE table_name='CABLE'",
        ).fetchone()
        definition = connection.execute(
            "SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id=?", (srs,),
        ).fetchone()[0]
        projection = Transformer.from_crs(CRS.from_user_input(definition), 3857, always_xy=True)
        shift = match["translation_3857"]
        for row in connection.execute("SELECT * FROM CABLE ORDER BY route_key,segment_index"):
            geometry = decode_geometry(row[column], srs)
            assert geometry.geom_type == "LineString"
            points = [[x - shift[0], y - shift[1]] for x, y in (
                projection.transform(point[0], point[1], errcheck=True)
                for point in geometry.coords
            )]
            route = routes[row["route_key"]]
            assert row["source_segment_kind"] == "line"
            if route:
                assert math.dist(route[-1], points[0]) < 1e-5, "Source segments must be contiguous"
                route.extend(points[1:])
            else:
                route.extend(points)
    return list(routes.values()), match
