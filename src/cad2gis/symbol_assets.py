"""Source-bound CAD symbol extraction for standard candidate review.

Run ``python -m cad2gis.symbol_assets --help``. The SQLite asset library is a
derived review product, not the source or semantic database.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import math
import re
import sqlite3
from contextlib import closing
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA = "cad2gis.symbol-assets.v1"


class _NoVectorGeometry(ValueError):
    def __init__(self, skipped):
        super().__init__('Selected CAD entities produced no vector geometry')
        self.skipped = skipped


def _create_symbol_schema(db):
    """Keep a valid asset library even when no source instance is renderable."""
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS symbols (symbol_id TEXT PRIMARY KEY, label TEXT NOT NULL, status TEXT NOT NULL, "
               "svg TEXT NOT NULL, svg_sha256 TEXT NOT NULL, source_handles_json TEXT NOT NULL, "
               "source_entities_json TEXT NOT NULL, diagnostics_json TEXT NOT NULL)")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _plain_svg(raw: str) -> str:
    """Inline exporter CSS for portable Qt SVG rendering; reject external assets."""
    root = ET.fromstring(raw)
    rules = {}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "style":
            for name, body in re.findall(r"\.([\w-]+)\s*\{([^}]+)\}", node.text or ""):
                rules[name] = dict(item.strip().split(":", 1) for item in body.split(";") if ":" in item)
    allowed = {"svg", "defs", "g", "path", "line", "polyline", "polygon", "circle", "ellipse", "rect", "style"}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in allowed:
            raise ValueError(f"SVG renderer emitted unsupported element: {node.tag}")
        for name in node.get("class", "").split():
            if name not in rules:
                raise ValueError(f"Unresolved SVG class: {name}")
            for key, value in rules[name].items():
                node.set(key.strip(), value.strip())
        node.attrib.pop("class", None)
        if any("href" in key.lower() or key.lower().startswith("on") or "url(" in value.lower()
               for key, value in node.attrib.items()):
            raise ValueError("External or active SVG content is not permitted")
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] == "style":
                parent.remove(child)
    # ezdxf renders glyphs as vector paths; no runtime font files are needed.
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    return ET.tostring(root, encoding="unicode")



def rasterize_svg(svg_text: str) -> bytes:
    """Rasterize the validated SVG on a centered, bounded 512px review canvas.

    resvg API: https://resvg-py.readthedocs.io/en/latest/api.html
    Glyphs are already paths; no remote assets or additional fonts are loaded.
    """
    import resvg_py
    root = ET.fromstring(_plain_svg(svg_text))
    root.set("width", "512")
    root.set("height", "512")
    root.set("preserveAspectRatio", "xMidYMid meet")
    return resvg_py.svg_to_bytes(svg_string=ET.tostring(root, encoding="unicode"),
                                 width=512, height=512, skip_system_fonts=True,
                                 background="#ffffff")


def _source_layout(document, entity, *, inherited=None):
    """Resolve linked entities through their owner chain, not a guessed block.

    ATTRIB, VERTEX and SEQEND can be owned by another entity rather than a
    BLOCK_RECORD. DXFGraphic.get_layout() assumes the latter in its block
    fallback. A known parent layout is used only for an unlinked child.
    """
    from ezdxf.lldxf.const import DXFKeyError

    visited = set()
    current = entity
    while True:
        handle = current.dxf.get("handle")
        identity = ("handle", str(handle).upper()) if handle else ("object", id(current))
        if identity in visited:
            raise ValueError(f"Cyclic CAD owner chain at entity {handle or current.dxftype()}")
        visited.add(identity)
        owner_handle = current.dxf.get("owner")
        if owner_handle in (None, "", "0"):
            return inherited
        owner = document.entitydb.get(str(owner_handle).upper())
        if owner is None:
            raise ValueError(f"Missing CAD owner {owner_handle} for entity {handle}")
        if owner.dxftype() == "BLOCK_RECORD":
            try:
                return document.layouts.get_layout_by_key(owner.dxf.handle)
            except DXFKeyError:
                return document.blocks.get_block_layout_by_handle(owner.dxf.handle)
        current = owner


def _render(document, entities, *, source_layout=None):
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
    from ezdxf.addons.drawing.config import BackgroundPolicy, Configuration

    skipped = []

    class RecordingFrontend(Frontend):
        def skip_entity(self, entity, msg):
            skipped.append({"handle": entity.dxf.get("handle", ""), "type": entity.dxftype(), "reason": msg})

    backend = svg.SVGBackend()
    context = RenderContext(document)
    entities = list(entities)
    source_layout = source_layout or next((resolved for entity in entities
                                          if (resolved := _source_layout(document, entity)) is not None), document.modelspace())
    context.set_current_layout(source_layout)
    context.current_layout_properties.set_colors("#ffffff", "#000000")
    frontend = RecordingFrontend(context, backend, Configuration(background_policy=BackgroundPolicy.OFF))
    frontend.draw_entities(entities)
    backend.finalize()
    backend.set_background("#ffffff00")
    if not backend.player().bbox().has_data:
        raise _NoVectorGeometry(skipped)
    # Qt SVG loses large (>32767) path coordinates in some render paths.
    # This normalization affects symbol presentation only, never CAD coordinates.
    result = _plain_svg(backend.get_string(layout.Page(0, 0, margins=layout.Margins.all(0.2)),
                       settings=layout.Settings(output_layers=False, output_coordinate_space=10000)))
    if not any(node.tag.rsplit("}", 1)[-1] in {"path", "line", "polyline", "polygon", "circle", "ellipse"}
               for node in ET.fromstring(result).iter()):
        raise _NoVectorGeometry(skipped)
    return result, skipped


def _overlay_text_entities(document, entities, spec: dict) -> list:
    """Build synthetic CAD text entities for a curated type label (for example FAT/FDT).

    The label is added as drawing entities so it is rendered to vector outlines by the
    same pipeline as everything else: no font is required when QGIS or the rasteriser
    draws the symbol, and the label cannot be replaced by layer styling. A slightly
    larger white glyph is drawn first, which reads as the white halo the reviewed layer
    labels use.
    """
    from ezdxf.bbox import extents
    from ezdxf.colors import rgb2int
    from ezdxf.enums import TextEntityAlignment

    label = str(spec.get("text", "")).strip()
    if not label:
        raise ValueError("Overlay text needs a non-empty text value")
    box = extents(entities)
    if box.size.x <= 0 or box.size.y <= 0:
        raise ValueError("Curated overlay text needs a measurable symbol extent")
    ratio = float(spec.get("height_ratio", 0.45))
    if not 0 < ratio <= 1:
        raise ValueError("Overlay text height_ratio must be within (0, 1]")
    # DXF text height is the nominal cap height. Capitals in Arial/Liberation Sans advance
    # roughly 0.88 cap heights each, and the white halo widens the label by halo_scale, so
    # the widest allowed label keeps a visible margin inside the frame.
    advance = float(spec.get("advance", 0.88))
    halo_scale = float(spec.get("halo_scale", 1.14))
    width_factor = float(spec.get("width_factor", 0.82))
    if advance <= 0 or halo_scale < 1 or not 0 < width_factor <= 1:
        raise ValueError("Overlay text advance/halo_scale/width_factor are invalid")
    height = min(box.size.y * ratio,
                 box.size.x * width_factor / (halo_scale * advance * max(len(label), 1)))
    style_name = "CAD2GIS_OVERLAY"
    if style_name not in document.styles:
        bold = str(spec.get("font_weight", "")).lower().startswith("bold")
        document.styles.new(style_name, dxfattribs={"font": "arialbd.ttf" if bold else "arial.ttf"})
    center = box.center
    layers = []
    for scale, color in ((halo_scale, spec.get("outline", "#ffffff")), (1.0, spec.get("fill", "#ff0000"))):
        text = document.modelspace().add_text(
            label, height=height * scale,
            dxfattribs={"style": style_name, "true_color": rgb2int(_rgb(color))},
        )
        text.set_placement((center.x, center.y), align=TextEntityAlignment.MIDDLE_CENTER)
        layers.append(text)
    return layers


def _rgb(value) -> tuple[int, int, int]:
    raw = str(value).strip().lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"Overlay text colour must be #rrggbb, got {value!r}")
    return tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))


def _closure(document, entities):
    """Record definition dependencies too, including unrendered dynamic variants."""
    facts, visited = [], set()

    def visit(entity, inherited_layout=None):
        handle = entity.dxf.get("handle", "")
        identity = ("handle", str(handle).upper()) if handle else ("object", id(entity))
        if identity in visited:
            return
        visited.add(identity)
        owner_layout = _source_layout(document, entity, inherited=inherited_layout)
        facts.append({"handle": handle, "type": entity.dxftype(), "attributes": entity.dxfattribs(),
                      "source_layer": entity.dxf.get("layer", ""),
                      "source_layout": owner_layout.name if owner_layout is not None else None})
        if entity.dxftype() == "INSERT":
            block = document.blocks.get(entity.dxf.name)
            if block is None:
                raise ValueError(f"Missing block definition: {entity.dxf.name}")
            for member in block:
                visit(member, block)
            for attribute in entity.attribs:
                visit(attribute, owner_layout)

    for entity in entities:
        visit(entity)
    return facts


def extract(source: Path, selection: Path, output: Path, *, font_dirs=(), max_symbols=5000) -> dict:
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.symbol-library-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'library'
        result = _extract(source, selection, staged, font_dirs=font_dirs, max_symbols=max_symbols)
        from .cad2gis_v3.artifact_io import inherit_output_permissions
        inherit_output_permissions(Path(temporary))
        staged.rename(output)
    # Paths in the command receipt must refer to the published library.
    return {key: str(output / Path(value).relative_to(staged))
            if isinstance(value, str) and value.startswith(str(staged)) else value
            for key, value in result.items()}


def _extract(source: Path, selection: Path, output: Path, *, font_dirs=(), max_symbols=5000) -> dict:
    """Create a new source-bound asset DB, SVG sidecars and an HTML review gallery."""
    from .runtime import configure_numeric_threads
    configure_numeric_threads()
    import ezdxf  # optional dependency; loaded only at this entry point
    from ezdxf.fonts import fonts

    for directory in font_dirs:
        if not Path(directory).is_dir():
            raise ValueError(f"Font directory does not exist: {directory}")
    if font_dirs:
        fonts.font_manager.build([str(Path(directory).resolve()) for directory in font_dirs])

    source = Path(source).resolve()
    output = Path(output).resolve()
    source_hash = _sha(source.read_bytes())
    selection_bytes = Path(selection).read_bytes()
    requested = json.loads(selection_bytes)
    if requested.get("source_sha256") != source_hash:
        raise ValueError("Symbol selection belongs to another source SHA-256")
    items = requested.get("symbols", [])
    if type(max_symbols) is not int or max_symbols < 1:
        raise ValueError("max_symbols must be a positive integer")
    if not items or len(items) > max_symbols:
        raise ValueError(f"Select 1 to {max_symbols} source-bound symbols")
    ids = [item.get("symbol_id", "") for item in items]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value) for value in ids):
        raise ValueError("Symbol IDs must be unique lowercase filename-safe identifiers")
    if output.exists():
        raise FileExistsError(f"Use a new symbol output directory: {output}")
    with tempfile.TemporaryDirectory(prefix="cad2gis-symbols-") as temporary:
        dxf = source
        backend_name = "ezdxf-direct"
        if source.suffix.lower() == ".dwg":
            from .reader.libredwg_cli import discover_libredwg_cli
            executable, _ = discover_libredwg_cli()
            if executable is None:
                raise RuntimeError("Optional DWG symbol extraction requires the LibreDWG runtime")
            dxf = Path(temporary) / "source.dxf"
            process = subprocess.run([str(executable), "-y", "-o", str(dxf), str(source)],
                                     capture_output=True, timeout=600, check=False)
            if process.returncode or not dxf.is_file():
                raise RuntimeError(f"LibreDWG symbol extraction failed: {process.stderr.decode(errors='replace')[-2000:]}")
            backend_name = "libredwg-dxf-ezdxf"
        elif source.suffix.lower() != ".dxf":
            raise ValueError("Symbol extraction accepts DWG or DXF")
        dxf_hash = _sha(dxf.read_bytes())
        document = ezdxf.readfile(dxf)
        assets = []
        layout_orders = {}
        for item in items:
            handles = item.get("handles", [])
            # A curated override may render geometry other than the bound entity
            # (for example a feature drawn as a POINT whose visible style is a
            # neighbouring polyline frame or a pole block). The bound handle keeps
            # the one-to-one binding contract; render_handles records what was drawn.
            render_handles = item.get("render_handles", handles)
            if not handles or len(handles) != len({str(handle).upper() for handle in handles}):
                raise ValueError("Each symbol requires a nonempty list of unique source handles")
            if not render_handles or len(render_handles) != len({str(handle).upper() for handle in render_handles}):
                raise ValueError("Each symbol requires a nonempty list of unique render handles")
            identities = [document.entitydb.get(str(handle).upper()) for handle in handles]
            if any(entity is None or not entity.is_alive for entity in identities):
                raise ValueError(f"Unresolved source identity handle in {item['symbol_id']}")
            entities = [document.entitydb.get(str(handle).upper()) for handle in render_handles]
            if any(entity is None or not entity.is_alive for entity in entities):
                raise ValueError(f"Unresolved render handle in {item['symbol_id']}")
            identity_layouts = [_source_layout(document, entity) for entity in identities]
            render_layouts = [_source_layout(document, entity) for entity in entities]
            source_owners = {layout.block_record_handle if layout is not None else None for layout in identity_layouts}
            if len(source_owners) != 1 or any((layout.block_record_handle if layout is not None else None) not in source_owners for layout in render_layouts):
                raise ValueError(f"Source identity and render handles span different layouts in {item['symbol_id']}")
            source_layout = identity_layouts[0]
            # Honor the CAD layout's redraw order, rather than selection-list order.
            if source_layout is not None:
                if source_layout.name not in layout_orders:
                    layout_orders[source_layout.name] = {
                        entity.dxf.handle: index
                        for index, entity in enumerate(source_layout.entities_in_redraw_order())}
                render_order = layout_orders[source_layout.name]
                entities.sort(key=lambda entity: render_order.get(entity.dxf.handle, len(render_order)))
            facts = _closure(document, entities)
            identity_facts = _closure(document, identities)
            text_facts = [fact for fact in facts if fact["type"] in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}]
            if text_facts:
                try:
                    fonts.font_manager.fallback_font_name()
                except fonts.FontNotFoundError as exc:
                    raise ValueError("No fonts available for source text; provide --font-dir before extracting symbols") from exc
            overlay = item.get("overlay_text")
            overlay_entities = _overlay_text_entities(document, entities, overlay) if overlay else []
            # The curated label is drawn after the frame geometry, so it stays on top.
            try:
                rendered, skipped = _render(document, [*entities, *overlay_entities], source_layout=source_layout)
            except _NoVectorGeometry as exc:
                # One unsupported/hidden instance must not erase every valid
                # candidate. A blank sidecar remains incomplete and unexportable.
                rendered = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" />'
                skipped = list(exc.skipped) + [
                    {"handle": entity.dxf.handle, "type": entity.dxftype(),
                     "reason": "selected_entities_produced_no_vector_geometry"} for entity in entities]
            selected_handles = {str(handle).upper() for handle in [*handles, *render_handles]}
            errors = [entry for entry in skipped if entry["reason"] != "invisible"
                      or str(entry.get("handle", "")).upper() in selected_handles]
            warnings = ["Candidate only: compare with the original CAD legend before use.",
                        "Glyph outlines may use font substitution; SVG layout is not survey geometry."]
            if any(fact["type"] == "INSERT" for fact in facts):
                warnings.append("INSERT visibility/dynamic state requires visual review; definition variants may overlap.")
            if errors:
                warnings.append("Renderer skipped entities; this symbol is incomplete.")
            elif skipped:
                warnings.append("CAD-invisible entities were excluded and recorded; verify the selected visibility state.")
            font_evidence = []
            for style_name in sorted({fact["attributes"].get("style", "Standard") for fact in text_facts}):
                style = document.styles.get(style_name)
                requested_font = style.dxf.get("font", "") if style else ""
                font_evidence.append({"style": style_name, "requested_font": requested_font,
                                      "available": bool(requested_font and fonts.font_manager.has_font(requested_font))})
            if any(not item["available"] for item in font_evidence):
                warnings.append("One or more source fonts are unavailable; substituted glyphs require review.")
            if overlay:
                diagnostics_overlay = {
                    "text": str(overlay.get("text", "")).strip(),
                    "fill": overlay.get("fill", "#ff0000"), "outline": overlay.get("outline", "#ffffff"),
                    "source": "curated overlay_entities rendered to vector outlines",
                }
            else:
                diagnostics_overlay = None
            geometry_basis = ('source_entity' if {str(handle).upper() for handle in handles}
                              == {str(handle).upper() for handle in render_handles} else 'explicit_render_selection')
            if geometry_basis != 'source_entity':
                warnings.append('Render handles differ from feature identity; review the explicit source association.')
            if overlay:
                warnings.append('Curated overlay text is presentation artwork, not text recovered from the CAD source.')
            assets.append({"symbol_id": item["symbol_id"], "label": str(item.get("label", item["symbol_id"])),
                           "handles": handles, "svg": rendered, "svg_sha256": _sha(rendered.encode()),
                           "render_handles": render_handles, "source_identity_entities": json.loads(_json(identity_facts)),
                           "source_layout": source_layout.name if source_layout is not None else None,
                           "geometry_basis": geometry_basis, "source_instance_fidelity_verified": False,
                           "status": "incomplete" if errors else "candidate", "warnings": warnings,
                           "fonts": font_evidence, "skipped": skipped, "source_entities": facts,
                           "overlay_text": diagnostics_overlay})
    if _sha(source.read_bytes()) != source_hash:
        raise ValueError("CAD source changed during extraction")
    output.mkdir(parents=True)
    (output / "symbols").mkdir()
    metadata = {"schema_version": SCHEMA, "source_sha256": source_hash, "source_name": source.name,
                "dxf_sha256": dxf_hash, "selection_sha256": _sha(selection_bytes),
                "backend": backend_name, "ezdxf_version": ezdxf.__version__, "auto_applied": False,
                "role": "derived_symbol_assets_not_source_or_semantic_authority",
                "font_directories": [str(Path(directory).resolve()) for directory in font_dirs]}
    with closing(sqlite3.connect(output / "symbols.sqlite3")) as db, db:
        _create_symbol_schema(db)
        db.executemany("INSERT INTO metadata VALUES (?, ?)", [(key, _json(value)) for key, value in metadata.items()])
        for asset in assets:
            db.execute("INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?)", (
                asset["symbol_id"], asset["label"], asset["status"], asset["svg"], asset["svg_sha256"],
                _json(asset["handles"]), _json(asset["source_entities"]),
                _json({"warnings": asset["warnings"], "skipped": asset["skipped"], "fonts": asset["fonts"],
                       "overlay_text": asset.get("overlay_text"), "render_handles": asset["render_handles"],
                       "source_identity_entities": asset["source_identity_entities"],
                       "source_layout": asset["source_layout"], "geometry_basis": asset["geometry_basis"],
                       "source_instance_fidelity_verified": False})))
            (output / "symbols" / f"{asset['symbol_id']}.svg").write_text(asset["svg"], encoding="utf-8")
    for asset in assets:
        png = rasterize_svg(asset["svg"])
        png_path = f"symbols/{asset['symbol_id']}.png"
        (output / png_path).write_bytes(png)
        asset.update(png_path=png_path, png_sha256=_sha(png), png_size=[512, 512])
    manifest = {**metadata, "symbols": [{key: value for key, value in asset.items() if key not in {"svg", "source_entities"}}
                                      for asset in assets]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "selection.json").write_bytes(selection_bytes)
    cards = "".join(f'<article><h2>{html.escape(asset["label"])}</h2><a href="symbols/{asset["symbol_id"]}.svg"><img src="symbols/{asset["symbol_id"]}.png" '
                    f'alt="{html.escape(asset["label"], quote=True)}"></a><p>{asset["status"]} · '
                    f'{html.escape(", ".join(asset["handles"]))}</p><p>{html.escape(" ".join(asset["warnings"]))}</p></article>'
                    for asset in assets)
    (output / "index.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width"><title>CAD symbol candidates</title>'
        '<style>body{font:16px system-ui;margin:2rem;background:#eef2f5}main{display:flex;flex-wrap:wrap;gap:1rem}'
        'article{background:white;padding:1.5rem;width:22rem}img{width:100%;height:220px;object-fit:contain}p{overflow-wrap:anywhere}</style>'
        '<h1>CAD symbol candidates</h1><p>Optional assets; conversion outputs remain unchanged. '
        'Review CAD visibility, text and shape before assigning a symbol.</p><main>' + cards + '</main></html>', encoding="utf-8")
    return {"output": str(output), "store": str(output / "symbols.sqlite3"), "symbols": len(assets),
            "status": "CANDIDATES_EXTRACTED"}


def export_qml(store: Path, symbol_id: str, output: Path, size_mm: float = 6.0) -> dict:
    """Export one explicitly selected candidate; never change a GPKG or semantic map."""
    if not math.isfinite(size_mm) or not 0 < size_mm <= 100:
        raise ValueError("Marker size must be finite and between 0 and 100 mm")
    if Path(output).exists():
        raise FileExistsError(output)
    with closing(sqlite3.connect(Path(store).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        metadata = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM metadata")}
        if metadata.get("schema_version") != SCHEMA:
            raise ValueError("Unsupported symbol store schema")
        row = db.execute("SELECT svg,svg_sha256,status FROM symbols WHERE symbol_id=?", (symbol_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown symbol ID: {symbol_id}")
    svg_text, digest, status = row
    if status != "candidate" or _sha(svg_text.encode()) != digest:
        raise ValueError("Incomplete or modified symbol cannot be exported")
    _plain_svg(svg_text)  # validate DB content before embedding
    root = ET.Element("qgis", version="3.44", styleCategories="Symbology")
    renderer = ET.SubElement(root, "renderer-v2", type="singleSymbol")
    symbols = ET.SubElement(renderer, "symbols")
    symbol = ET.SubElement(symbols, "symbol", type="marker", name="0", alpha="1", clip_to_extent="1")
    layer = ET.SubElement(symbol, "layer", {"class": "SvgMarker", "enabled": "1", "locked": "0"})
    options = ET.SubElement(layer, "Option", type="Map")
    for name, value in {"name": "base64:" + base64.b64encode(svg_text.encode()).decode(),
                        "size": str(size_mm), "size_unit": "MM", "angle": "0"}.items():
        ET.SubElement(options, "Option", name=name, type="QString", value=value)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with Path(output).open("xb") as stream:
        stream.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return {"qml": str(output), "symbol_id": symbol_id, "source_sha256": metadata["source_sha256"],
            "svg_sha256": digest, "status": "CANDIDATE_STYLE_EXPORTED", "auto_applied": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    extraction = commands.add_parser("extract", help="Extract a source-hash-bound handle selection into a new asset library")
    extraction.add_argument("--source", required=True, type=Path)
    extraction.add_argument("--selection", required=True, type=Path)
    extraction.add_argument("--output", required=True, type=Path)
    extraction.add_argument("--font-dir", action="append", default=[], type=Path,
                            help="Optional local CAD/TTF font directory; repeatable, never downloaded")
    qml = commands.add_parser("qml", help="Export one candidate as portable QML for explicit visual review")
    qml.add_argument("--store", required=True, type=Path)
    qml.add_argument("--symbol-id", required=True)
    qml.add_argument("--output", required=True, type=Path)
    qml.add_argument("--size-mm", type=float, default=6.0)
    args = parser.parse_args(argv)
    try:
        result = (extract(args.source, args.selection, args.output, font_dirs=args.font_dir) if args.command == "extract"
                  else export_qml(args.store, args.symbol_id, args.output, args.size_mm))
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        parser.exit(2, f"Symbol assets: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
