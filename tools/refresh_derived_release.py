"""Regenerate derived QGIS presentation and seal the already prepared release."""
import json
import shutil
from pathlib import Path

from cad2gis.batch import digest, write_index, write_json
from cad2gis.delivery import qgis_project, seal_delivery

root = Path(__file__).resolve().parents[1] / "pages-delivery" / "nine-drawings"
for project in root.rglob("delivery.qgz"):
    qgis_project(project.parent / "delivery.gpkg", project.parent / "styles", project)
report = json.loads((root / "batch-report.json").read_text(encoding="utf-8"))
catalog = json.loads((root / "assets" / "catalog.json").read_text(encoding="utf-8"))
names = {p["id"]: p["display_name"] for p in catalog["projects"]}
for project in catalog["projects"]:
    fixture_path = root / "assets" / project["fixture"]
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cables = fixture.get("layers", {}).get("CABLE", {}).get("features", [])
    measured = sum(f.get("properties", {}).get("length_source") == "dwg_dimension" for f in cables)
    fixture["run"].setdefault("validation", {})["segment_delivery"] = {
        "count": len(cables), "measured": measured, "unmeasured": len(cables) - measured,
    }
    fixture["provenance"]["publication_boundary"] = fixture["run"]["demo"]["publication_boundary"]
    project["map_reference"] = fixture["run"]["crs"]["target_crs"]
    write_json(fixture_path, fixture)
write_json(root / "assets" / "catalog.json", catalog)
for record in report["drawings"]:
    record["name"] = names[record["id"]]
    record["preview"] = record["id"] + "/process/source-delivery-overlay.png"
    record["links"]["Web 转化台"] = "../workspace/?demo=1&project=" + record["id"]
    if (root / "ERRATA.md").exists():
        record["links"]["Linux 复跑与历史勘误"] = "ERRATA.md"
        shutil.copy2(root / "ERRATA.md", root / record["id"] / "ERRATA.md")
    if record["id"] in {"drawing-03", "drawing-09"}:
        record["error"] = "历史 AutoCAD 属性串读影响部分 BOITE；请先阅读勘误。"
    if (root / record["id"] / "process" / "qgis-labels-detail.png").exists():
        record["links"]["QGIS 标签实测图"] = record["id"] + "/process/qgis-labels-detail.png"
    if record["id"] == "drawing-03":
        for partition in ("EMR28560", "EMR29619"):
            record["links"][partition + " QGIS"] = "drawing-03/" + partition + "/delivery.qgz"
    seal_delivery(root / record["id"], replace=True)
write_json(root / "batch-report.json", report)
write_index(root, report)
manifest = json.loads((root / "publication.json").read_text(encoding="utf-8"))
manifest["files"] = {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name != "publication.json"}
write_json(root / "publication.json", manifest)
print("Refreshed and sealed nine derived deliveries")
