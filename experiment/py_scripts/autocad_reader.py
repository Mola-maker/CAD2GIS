"""Direct, read-only AutoCAD DWG ingestion for the experiment pipeline.

The reader deliberately does not create DXF files.  On Windows it opens the
DWG through AutoCAD 2027's COM database interface and enumerates model/layout
entities, attributes, display styles, and topology sheets in one pass.
"""

from __future__ import annotations

import math
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from apd_rules import is_telecom_block, link_apd_annotations


AUTOCAD_PROGIDS = ("AutoCAD.Application.26", "AutoCAD.Application")
OBJECTDBX_PROGID = "ObjectDBX.AxDbDocument.26"
DEFAULT_ACCORECONSOLE = Path("C:/Program Files/Autodesk/AutoCAD 2027/accoreconsole.exe")

_AUTOLISP_EXTRACTOR = r'''
(vl-load-com)
(defun c2g-get (code data default / pair)
  (setq pair (assoc code data))
  (if pair (cdr pair) default))
(defun c2g-escape (value / result index code)
  (setq value (if value value "") result "" index 1)
  (while (<= index (strlen value))
    (setq code (ascii (substr value index 1)))
    (setq result (strcat result (cond
      ((= code 92) "\\\\") ((= code 9) "\\t")
      ((= code 10) "\\n") ((= code 13) "\\r")
      (T (chr code)))))
    (setq index (1+ index))) result)
(defun c2g-num (value) (if value (rtos value 2 16) ""))
(defun c2g-point (point)
  (if point (strcat (c2g-num (car point)) "," (c2g-num (cadr point))) ""))
(defun c2g-add-point (result point)
  (if (= result "") (c2g-point point) (strcat result ";" (c2g-point point))))
(defun c2g-points (entity data kind / result item next nextdata nextkind)
  (setq result "")
  (cond
    ((= kind "LINE")
      (setq result (c2g-add-point result (c2g-get 10 data nil)))
      (setq result (c2g-add-point result (c2g-get 11 data nil))))
    ((= kind "LWPOLYLINE")
      (foreach item data (if (= (car item) 10) (setq result (c2g-add-point result (cdr item))))))
    ((= kind "POLYLINE")
      (setq next (entnext entity))
      (while next
        (setq nextdata (entget next) nextkind (c2g-get 0 nextdata ""))
        (cond
          ((= nextkind "VERTEX") (setq result (c2g-add-point result (c2g-get 10 nextdata nil))))
          ((= nextkind "SEQEND") (setq next nil)))
        (if next (setq next (entnext next)))))
    ((= kind "DIMENSION")
      (setq result (c2g-add-point result (c2g-get 13 data nil)))
      (setq result (c2g-add-point result (c2g-get 14 data nil))))
    (T (setq result (c2g-add-point result (c2g-get 10 data nil))))) result)
(defun c2g-text (data / result item)
  (setq result (c2g-get 1 data ""))
  (foreach item data (if (= (car item) 3) (setq result (strcat result (cdr item))))) result)
(defun c2g-attributes (entity / result next data kind tag value)
  (setq result "" next (entnext entity))
  (while next
    (setq data (entget next) kind (c2g-get 0 data ""))
    (cond
      ((= kind "ATTRIB")
        (setq tag (c2g-escape (c2g-get 2 data "")) value (c2g-escape (c2g-get 1 data "")))
        (setq result (if (= result "") (strcat tag "=" value) (strcat result "|" tag "=" value))))
      ((= kind "SEQEND") (setq next nil)))
    (if next (setq next (entnext next)))) result)
(defun c2g-supported (kind)
  (wcmatch kind "LINE,LWPOLYLINE,POLYLINE,CIRCLE,ARC,POINT,INSERT,TEXT,MTEXT"))
(defun c2g-write-entity (file entity layoutoverride / data kind handle layer layout color truecolor linetype lineweight rotation closed block text attrs points radius start end row flags layerdata layercolor layertruecolor layerlinetype layerlineweight scalex scaley scalez)
  (setq data (entget entity) kind (c2g-get 0 data ""))
  (if (/= kind "")
    (progn
      (setq handle (c2g-get 5 data "") layer (c2g-get 8 data "0"))
      (setq layout (if layoutoverride layoutoverride (c2g-get 410 data "Model")))
      (setq color (c2g-get 62 data 256) truecolor (c2g-get 420 data -1))
      (setq linetype (c2g-get 6 data "ByLayer") lineweight (c2g-get 370 data -1) rotation (c2g-get 50 data 0.0))
      (setq layerdata (tblsearch "LAYER" layer))
      (setq layercolor (abs (c2g-get 62 layerdata 7)) layertruecolor (c2g-get 420 layerdata -1))
      (setq layerlinetype (c2g-get 6 layerdata "Continuous") layerlineweight (c2g-get 370 layerdata -1))
      (setq flags (c2g-get 70 data 0) closed (if (or (= kind "CIRCLE") (= 1 (logand flags 1))) 1 0))
      (setq block (if (= kind "INSERT") (c2g-get 2 data "") ""))
      (setq scalex (if (= kind "INSERT") (c2g-get 41 data 1.0) 1.0))
      (setq scaley (if (= kind "INSERT") (c2g-get 42 data 1.0) 1.0))
      (setq scalez (if (= kind "INSERT") (c2g-get 43 data 1.0) 1.0))
      (setq text (if (or (= kind "TEXT") (= kind "MTEXT") (= kind "DIMENSION")) (c2g-text data) ""))
      (setq attrs (if (= kind "INSERT") (c2g-attributes entity) ""))
      (setq points (c2g-points entity data kind))
      (setq radius (if (= kind "DIMENSION") (c2g-get 42 data 0.0) (c2g-get 40 data 0.0)))
      (setq start (c2g-get 50 data 0.0) end (c2g-get 51 data 6.283185307179586))
      (setq row (strcat
        (c2g-escape kind) (chr 9) (c2g-escape handle) (chr 9) (c2g-escape layer) (chr 9)
        (c2g-escape layout) (chr 9) (itoa color) (chr 9) (itoa truecolor) (chr 9)
        (c2g-escape linetype) (chr 9) (itoa lineweight) (chr 9) (c2g-num rotation) (chr 9)
        (itoa closed) (chr 9) (c2g-escape block) (chr 9) (c2g-escape text) (chr 9)
        attrs (chr 9) points (chr 9) (c2g-num radius) (chr 9) (c2g-num start) (chr 9) (c2g-num end) (chr 9)
        (itoa layercolor) (chr 9) (itoa layertruecolor) (chr 9) (c2g-escape layerlinetype) (chr 9) (itoa layerlineweight) (chr 9)
        (c2g-num scalex) (chr 9) (c2g-num scaley) (chr 9) (c2g-num scalez)))
      (write-line row file))))
(defun cad2gis-export (path / file selection index entity blockdata blockname blockentity blockkind)
  (setq file (open path "w" "utf8"))
  (write-line (strcat
    "DOCUMENT_METADATA" (chr 9) "" (chr 9) "0" (chr 9) "DOCUMENT" (chr 9)
    "256" (chr 9) "-1" (chr 9) "ByLayer" (chr 9) "-1" (chr 9) "0" (chr 9) "0" (chr 9)
    "" (chr 9) (c2g-escape (strcat "CGEOCS=" (getvar "CGEOCS") ";INSUNITS=" (itoa (getvar "INSUNITS"))))
    (chr 9) "" (chr 9) "" (chr 9) "0" (chr 9) "0" (chr 9) "0") file)
  (setq selection (ssget "_X"))
  (if selection
    (progn
      (setq index 0)
      (while (< index (sslength selection))
        (c2g-write-entity file (ssname selection index) nil)
        (setq index (1+ index)))))
  (setq blockdata (tblnext "BLOCK" T))
  (while blockdata
    (setq blockname (c2g-get 2 blockdata ""))
    (if (and (/= (strcase blockname) "*MODEL_SPACE") (not (wcmatch (strcase blockname) "*PAPER_SPACE*")))
      (progn
        (setq blockentity (entnext (tblobjname "BLOCK" blockname)))
        (while blockentity
          (setq blockkind (c2g-get 0 (entget blockentity) ""))
          (if (= blockkind "ENDBLK")
            (setq blockentity nil)
            (progn
              (c2g-write-entity file blockentity (strcat "BLOCKDEF:" blockname))
              (setq blockentity (entnext blockentity)))))))
    (setq blockdata (tblnext "BLOCK")))
  (close file) (princ))
'''

_TEXT_OBJECTS = {"ACDBTEXT", "ACDBMTEXT", "ACDBATTRIBUTEREFERENCE"}
_TOPOLOGY_LAYOUT = re.compile(r"(?i)(TOPOLOGY|SPLICING|SCHEMATIC|DIAGRAM)")
_PLAN_LAYOUT = re.compile(r"(?i)(FDT(?:[-_ ]?(?:ALL|\d+))?|PLAN|NETWORK)")
_STYLE_LAYOUT = re.compile(r"(?i)(LEGEND|CABLE[ _-]*TYPE|SYMBOL)")
_EQUIPMENT_LAYOUT = re.compile(r"(?i)(FDT[ _-]*LAYOUT|EQUIPMENT)")
_SUMMARY_TEXT = re.compile(r"(?i)(DESIGN\s+SUMMARY|TOTAL\s+(?:NEW|POLE|CABLE|FAT|FDT))")
_TITLE_TEXT = re.compile(
    r"(?i)(APPROVAL\s+MATRIX|DRAWING\s+SIGN|REVISION|PROJECT\s+NAME|DRAWING\s+NO|"
    r"VENDOR|CONTRACTOR|PLANNING\s+DEPT|OPERATION\s+DEPT)"
)
_LEGEND_TEXT = re.compile(r"(?i)^(?:\s*)(LEGEND|CABLE\s+TYPE)(?:\s*)$")
_TITLE_BLOCK_NAME = re.compile(r"(?i)(ETIKET|TITLE|FRAME|BORDER|CARTOUCHE)")


def classify_layout_role(layout_name: str) -> str:
    """Classify an AutoCAD layout without treating evidence sheets as GIS."""
    name = (layout_name or "").strip()
    if name.upper().startswith("BLOCKDEF:"):
        return "block_definition"
    if name.casefold() == "model":
        return "model"
    if _TOPOLOGY_LAYOUT.search(name):
        return "topology"
    if _STYLE_LAYOUT.search(name):
        return "style_legend"
    if _EQUIPMENT_LAYOUT.search(name):
        return "equipment_layout"
    if _PLAN_LAYOUT.search(name):
        return "plan"
    return "layout"


def _unescape_tsv(value):
    result, index = [], 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            code = value[index + 1]
            result.append({"t": "\t", "n": "\n", "r": "\r", "\\": "\\"}.get(code, code))
            index += 2
        else:
            result.append(value[index])
            index += 1
    return "".join(result)


def _parse_bulk_attributes(value):
    result = {}
    for pair in value.split("|") if value else ():
        if "=" not in pair:
            continue
        key, item = pair.split("=", 1)
        key, item = _unescape_tsv(key).upper(), _unescape_tsv(item)
        if key and item:
            result[key] = item
    return result


def _parse_bulk_points(value):
    points = []
    for item in value.split(";") if value else ():
        try:
            x, y = item.split(",", 1)
            points.append((float(x), float(y)))
        except (TypeError, ValueError):
            continue
    return points


def _record_from_bulk_row(columns):
    if len(columns) not in {17, 21, 24}:
        return None
    base = columns[:17]
    (kind, handle, layer, layout, aci, truecolor, linetype, lineweight,
     rotation, closed, block_name, text, attributes, point_text,
     radius_text, start_text, end_text) = base
    layer_aci, layer_truecolor, layer_linetype, layer_lineweight = (
        columns[17:21] if len(columns) >= 21 else ("7", "-1", "Continuous", "-1")
    )
    scale_x, scale_y, scale_z = columns[21:24] if len(columns) == 24 else ("1", "1", "1")
    kind = _unescape_tsv(kind).upper()
    points = _parse_bulk_points(point_text)
    if kind in {"CIRCLE", "ARC"} and points:
        center = points[0]
        radius = float(radius_text or 0.0)
        start = 0.0 if kind == "CIRCLE" else float(start_text or 0.0)
        end = 2 * math.pi if kind == "CIRCLE" else float(end_text or 2 * math.pi)
        if end <= start:
            end += 2 * math.pi
        segments = 48 if kind == "CIRCLE" else 24
        points = [
            (center[0] + radius * math.cos(start + (end - start) * index / segments),
             center[1] + radius * math.sin(start + (end - start) * index / segments))
            for index in range(segments + 1)
        ]
    parsed_attributes = _parse_bulk_attributes(attributes)
    parsed_text = _unescape_tsv(text)
    if parsed_attributes:
        parsed_text = "\n".join(f"{key}={value}" for key, value in sorted(parsed_attributes.items()))
    centroid = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    ) if points else (0.0, 0.0)
    entity_aci = int(aci or 256)
    layer_aci_value = abs(int(layer_aci or 7))
    entity_true_color = int(truecolor or -1)
    layer_true_color = int(layer_truecolor or -1)
    effective_aci = layer_aci_value if entity_aci == 256 else entity_aci
    effective_true_color = entity_true_color if entity_true_color >= 0 else layer_true_color
    effective_linetype = (
        _unescape_tsv(layer_linetype) or "Continuous"
        if _unescape_tsv(linetype).casefold() == "bylayer"
        else _unescape_tsv(linetype)
    )
    effective_lineweight = int(layer_lineweight or -1) if int(lineweight or -1) < 0 else int(lineweight)
    layout_name = _unescape_tsv(layout) or "Model"
    layout_role = classify_layout_role(layout_name)
    object_names = {
        "LINE": "ACDBLINE", "LWPOLYLINE": "ACDBLWPOLYLINE",
        "POLYLINE": "ACDBPOLYLINE", "CIRCLE": "ACDBCIRCLE",
        "ARC": "ACDBARC", "POINT": "ACDBPOINT", "INSERT": "ACDBBLOCKREFERENCE",
        "TEXT": "ACDBTEXT", "MTEXT": "ACDBMTEXT",
        "DIMENSION": "ACDBDIMENSION",
    }
    return {
        "handle": _unescape_tsv(handle), "object_name": object_names.get(kind, f"ACDB{kind}"),
        "dwg_type_name": kind, "layout": layout_name, "layout_role": layout_role,
        "cad_role": layout_role, "layer": _unescape_tsv(layer) or "0",
        "points": points, "centroid": centroid, "closed": closed == "1",
        "text": parsed_text, "block_name": _unescape_tsv(block_name),
        "block_attributes": parsed_attributes, "aci_color": effective_aci,
        "true_color": f"#{effective_true_color & 0xFFFFFF:06X}" if effective_true_color >= 0 else "",
        "linetype": effective_linetype or "Continuous", "lineweight": effective_lineweight,
        "rotation": float(rotation or 0.0),
        "entity_aci_color": entity_aci, "layer_aci_color": layer_aci_value,
        "entity_true_color": f"#{entity_true_color & 0xFFFFFF:06X}" if entity_true_color >= 0 else "",
        "layer_true_color": f"#{layer_true_color & 0xFFFFFF:06X}" if layer_true_color >= 0 else "",
        "entity_linetype": _unescape_tsv(linetype) or "ByLayer",
        "layer_linetype": _unescape_tsv(layer_linetype) or "Continuous",
        "entity_lineweight": int(lineweight or -1),
        "layer_lineweight": int(layer_lineweight or -1),
        "scale_x": float(scale_x or 1.0), "scale_y": float(scale_y or 1.0),
        "scale_z": float(scale_z or 1.0),
        "dimension_value": float(radius_text or 0.0) if kind == "DIMENSION" else None,
    }


def _extract_records_with_core_console(dwg_path, accoreconsole=DEFAULT_ACCORECONSOLE):
    if not accoreconsole.is_file():
        raise RuntimeError(f"AutoCAD Core Console not found: {accoreconsole}")
    with tempfile.TemporaryDirectory(prefix="cad2gis-autocad-db-") as temp_name:
        workspace = Path(temp_name)
        lisp_path = workspace / "extract.lsp"
        script_path = workspace / "extract.scr"
        output_path = workspace / "entities.tsv"
        lisp_path.write_text(_AUTOLISP_EXTRACTOR, encoding="utf-8")
        lisp_arg = lisp_path.as_posix()
        output_arg = output_path.as_posix()
        script_path.write_text(
            "FILEDIA\n0\nCMDDIA\n0\nSECURELOAD\n0\n"
            f'(load "{lisp_arg}")\n(cad2gis-export "{output_arg}")\n'
            "_.QUIT\n",
            encoding="utf-8",
        )
        command = [
            str(accoreconsole), "/readonly", "/i", str(Path(dwg_path).resolve()),
            "/s", str(script_path),
        ]
        completed = subprocess.run(command, capture_output=True, timeout=120, check=False)
        if completed.returncode != 0 or not output_path.is_file():
            detail = (completed.stdout + completed.stderr)[-4000:].decode("utf-16-le", errors="replace")
            raise RuntimeError(f"AutoCAD direct database extraction failed ({completed.returncode}): {detail}")
        grouped = {}
        with output_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
            for line in stream:
                record = _record_from_bulk_row(line.rstrip("\r\n").split("\t"))
                if record is not None:
                    grouped.setdefault(record["layout"], []).append(record)
        result = []
        for layout_name in sorted(grouped, key=lambda value: (value.casefold() != "model", value.casefold())):
            records = grouped[layout_name]
            layout_role = classify_layout_role(layout_name)
            if layout_role in {"model", "plan"}:
                partition_plan_roles(records)
                if layout_role == "model":
                    partition_model_legend(records)
            elif layout_role == "block_definition":
                for record in records:
                    record["cad_role"] = "plan"
                partition_plan_roles(records)
                for record in records:
                    if record["cad_role"] == "plan":
                        record["cad_role"] = "block_definition"
            result.append((layout_name, layout_role, records))
        return result


def _is_com_busy(exc):
    code = getattr(exc, "hresult", None)
    if code is None and getattr(exc, "args", None):
        code = exc.args[0]
    return code in {-2147418111, -2147417846}


def _retry_com(callback, retries=80):
    for attempt in range(retries):
        try:
            return callback()
        except Exception as exc:
            if not _is_com_busy(exc) or attempt == retries - 1:
                raise
            try:
                import pythoncom
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def _safe_get(obj, name, default=None):
    try:
        value = _retry_com(lambda: getattr(obj, name))
    except Exception:
        return default
    return default if value is None else value


def _iter_com_collection(collection):
    count = int(_retry_com(lambda: collection.Count))
    for index in range(count):
        yield _retry_com(lambda index=index: collection.Item(index))


def _select_model_collections(document, assign_fc):
    """Use AutoCAD's native selection engine to avoid scanning every object."""
    import pythoncom
    import win32com.client

    selection_sets = _safe_get(document, "SelectionSets")
    layers = _safe_get(document, "Layers")
    if selection_sets is None or layers is None:
        return []
    relevant_layers = []
    for layer in _iter_com_collection(layers):
        layer_name = str(_safe_get(layer, "Name", ""))
        fc_name, _, _, _ = assign_fc(layer_name, "")
        if fc_name != "fc_misc":
            relevant_layers.append(layer_name)

    selected = []
    specifications = [("TEXT,MTEXT,INSERT", None)]
    if relevant_layers:
        specifications.append(("LINE,LWPOLYLINE,POLYLINE,CIRCLE,ARC,POINT", ",".join(relevant_layers)))
    try:
        for entity_types, layer_names in specifications:
            name = f"CAD2GIS_{uuid.uuid4().hex[:12]}"
            selection = _retry_com(lambda name=name: selection_sets.Add(name))
            filter_types = [0]
            filter_values = [entity_types]
            if layer_names:
                filter_types.append(8)
                filter_values.append(layer_names)
            type_variant = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_I2, filter_types
            )
            data_variant = win32com.client.VARIANT(
                pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, filter_values
            )
            _retry_com(lambda: selection.Select(5, None, None, type_variant, data_variant))
            selected.append(selection)
        return selected
    except Exception:
        for selection in selected:
            try:
                _retry_com(selection.Delete)
            except Exception:
                pass
        return []


def _xy(value):
    try:
        return float(value[0]), float(value[1])
    except Exception:
        return None


def _flat_points(values, stride):
    try:
        numbers = [float(value) for value in values]
    except Exception:
        return []
    return [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, stride)]


def _plain_text(value):
    text = str(value or "").replace("\\P", "\n")
    text = re.sub(r"\\[A-Za-z][^;]*;", "", text)
    return text.replace("{", "").replace("}", "").strip()


def _entity_text(entity, object_name):
    if object_name in _TEXT_OBJECTS:
        return _plain_text(_safe_get(entity, "TextString", _safe_get(entity, "Contents", "")))
    return ""


def _block_attributes(entity):
    if not bool(_safe_get(entity, "HasAttributes", False)):
        return {}
    try:
        attributes = _retry_com(entity.GetAttributes)
    except Exception:
        return {}
    result = {}
    for attribute in attributes or ():
        tag = str(_safe_get(attribute, "TagString", "")).strip().upper()
        value = _plain_text(_safe_get(attribute, "TextString", ""))
        if tag and value:
            result[tag] = value
    return result


def _entity_points(entity, object_name):
    if object_name == "ACDBLINE":
        return [point for point in (_xy(_safe_get(entity, "StartPoint")), _xy(_safe_get(entity, "EndPoint"))) if point]
    if object_name in {"ACDBPOLYLINE", "ACDB2DPOLYLINE", "ACDB3DPOLYLINE"}:
        return _flat_points(_safe_get(entity, "Coordinates", ()), 3)
    if object_name in {"ACDBLWPOLYLINE", "ACDBLEADER"}:
        return _flat_points(_safe_get(entity, "Coordinates", ()), 2)
    if object_name in {"ACDBPOINT", "ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}:
        point = _xy(_safe_get(entity, "InsertionPoint", _safe_get(entity, "Coordinates")))
        return [point] if point else []
    if object_name in _TEXT_OBJECTS:
        point = _xy(_safe_get(entity, "InsertionPoint", _safe_get(entity, "TextAlignmentPoint")))
        return [point] if point else []
    if object_name in {"ACDBCIRCLE", "ACDBARC"}:
        center = _xy(_safe_get(entity, "Center"))
        radius = float(_safe_get(entity, "Radius", 0.0) or 0.0)
        if not center or radius <= 0:
            return []
        start = 0.0 if object_name == "ACDBCIRCLE" else float(_safe_get(entity, "StartAngle", 0.0))
        end = 2 * math.pi if object_name == "ACDBCIRCLE" else float(_safe_get(entity, "EndAngle", 2 * math.pi))
        if end <= start:
            end += 2 * math.pi
        segments = 48 if object_name == "ACDBCIRCLE" else 24
        return [
            (center[0] + radius * math.cos(start + (end - start) * i / segments),
             center[1] + radius * math.sin(start + (end - start) * i / segments))
            for i in range(segments + 1)
        ]
    return []


def _true_color(entity):
    color = _safe_get(entity, "TrueColor")
    value = _safe_get(color, "ColorValue") if color is not None else None
    try:
        return f"#{int(value) & 0xFFFFFF:06X}"
    except (TypeError, ValueError):
        return ""


def extract_com_entity(
    entity, layout_name, layout_role,
    object_name_hint=None, layer_hint=None, block_name_hint=None,
):
    """Convert one AutoCAD COM entity into a deterministic neutral record."""
    object_name = str(object_name_hint or _safe_get(entity, "ObjectName", "")).upper()
    points = _entity_points(entity, object_name)
    text = _entity_text(entity, object_name)
    block_name = ""
    attributes = {}
    if object_name in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}:
        block_name = str(
            block_name_hint
            or _safe_get(entity, "EffectiveName", _safe_get(entity, "Name", ""))
        )
        attributes = _block_attributes(entity)
        if attributes:
            text = "\n".join(f"{key}={value}" for key, value in sorted(attributes.items()))
    if not points and not text:
        return None
    centroid = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    ) if points else (0.0, 0.0)
    return {
        "handle": str(_safe_get(entity, "Handle", "")),
        "object_name": object_name,
        "dwg_type_name": object_name.removeprefix("ACDB").upper(),
        "layout": layout_name,
        "layout_role": layout_role,
        "cad_role": layout_role,
        "layer": str(layer_hint if layer_hint is not None else _safe_get(entity, "Layer", "0")),
        "points": points,
        "centroid": centroid,
        "closed": bool(_safe_get(entity, "Closed", object_name == "ACDBCIRCLE")),
        "text": text,
        "block_name": block_name,
        "block_attributes": attributes,
        "aci_color": int(_safe_get(entity, "Color", 256) or 256),
        "true_color": _true_color(entity),
        "linetype": str(_safe_get(entity, "Linetype", "ByLayer")),
        "lineweight": int(_safe_get(entity, "Lineweight", -1) or -1),
        "rotation": float(_safe_get(entity, "Rotation", 0.0) or 0.0),
    }


def partition_plan_roles(records):
    """Mark legend/summary/title/frame zones inside a model or plan sheet."""
    points = [point for record in records for point in record.get("points", ())]
    if not points:
        return records
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    width, height = max(max_x - min_x, 1e-12), max(max_y - min_y, 1e-12)
    legend_anchors = [r for r in records if _LEGEND_TEXT.search(r.get("text", ""))]
    cable_anchors = [r for r in records if re.search(r"(?i)^\s*CABLE\s+TYPE\s*$", r.get("text", ""))]
    summary_anchors = [r for r in records if re.search(r"(?i)DESIGN\s+SUMMARY", r.get("text", ""))]
    title_anchors = [r for r in records if _TITLE_TEXT.search(r.get("text", ""))]
    legend_x = min((r["centroid"][0] for r in legend_anchors), default=None)
    legend_floor = min((r["centroid"][1] for r in cable_anchors), default=min_y + 0.42 * height) - 0.12 * height

    for record in records:
        x, y = record["centroid"]
        text = record.get("text", "")
        xs = [point[0] for point in record.get("points", ())]
        ys = [point[1] for point in record.get("points", ())]
        span_x = max(xs) - min(xs) if xs else 0.0
        span_y = max(ys) - min(ys) if ys else 0.0
        if _TITLE_BLOCK_NAME.search(record.get("block_name", "")):
            record["cad_role"] = "title_block"
        elif record.get("closed") and span_x >= 0.85 * width and span_y >= 0.85 * height:
            record["cad_role"] = "frame"
        elif legend_x is not None and x >= legend_x - 0.10 * width:
            record["cad_role"] = "style_legend" if y >= legend_floor else "title_block"
        elif _SUMMARY_TEXT.search(text) or (
            summary_anchors and x <= min_x + 0.36 * width and y <= min_y + 0.25 * height
        ):
            record["cad_role"] = "design_summary"
        elif _TITLE_TEXT.search(text) or (title_anchors and y <= min_y + 0.12 * height):
            record["cad_role"] = "title_block"
    return records


def partition_model_legend(records):
    """Separate the isolated APD legend cluster from geographic model data."""
    inserts = [
        record for record in records
        if record.get("object_name") in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}
        and record.get("points")
    ]
    if len(inserts) < 10:
        return records
    ordered_x = sorted(record["centroid"][0] for record in inserts)
    gaps = [(ordered_x[index + 1] - ordered_x[index], index) for index in range(len(ordered_x) - 1)]
    largest_gap, gap_index = max(gaps, default=(0.0, 0))
    span = max(ordered_x[-1] - ordered_x[0], 1.0)
    cutoff = (ordered_x[gap_index] + ordered_x[gap_index + 1]) / 2.0
    high_count = sum(1 for value in ordered_x if value > cutoff)
    if largest_gap < max(100.0, 0.25 * span) or high_count > max(20, int(0.10 * len(inserts))):
        return records
    for record in records:
        if record.get("centroid", (float("-inf"), 0.0))[0] > cutoff and record.get("cad_role") == "model":
            record["cad_role"] = "style_legend"
    return records


def _point_wkt(x, y):
    return f"POINT ({x:.12g} {y:.12g})"


def _line_wkt(points):
    return "LINESTRING (" + ", ".join(f"{x:.12g} {y:.12g}" for x, y in points) + ")"


def _polygon_wkt(points):
    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return "POLYGON ((" + ", ".join(f"{x:.12g} {y:.12g}" for x, y in ring) + "))"


def _evidence_item(record, source_name, kind):
    return {
        "output_kind": kind,
        "source_file": source_name,
        "layout": record["layout"],
        "cad_role": record["cad_role"],
        "handle": record["handle"],
        "layer": record["layer"],
        "dwg_type_name": record["dwg_type_name"],
        "text": record["text"],
        "block_name": record["block_name"],
        "aci_color": record["aci_color"],
        "true_color": record["true_color"],
        "linetype": record["linetype"],
        "lineweight": record["lineweight"],
        "rotation": record["rotation"],
        "dimension_value": record.get("dimension_value"),
        "native_points": json.dumps(record.get("points", []), separators=(",", ":")),
        "terminal_disposition": "unresolved",
    }


def _feature_item(record, source_name, reproject_point, assign_fc, classify_block, extract_attributes):
    points = [reproject_point(float(x), float(y)) for x, y in record["points"]]
    if not points:
        return None
    cx = sum(point[0] for point in points) / len(points)
    cy = sum(point[1] for point in points) / len(points)
    if not (-180 <= cx <= 180 and -90 <= cy <= 90):
        return None
    is_insert = record["object_name"] in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}
    if is_insert:
        if not is_telecom_block(record["block_name"]):
            return None
        fc_name = classify_block(record["block_name"])
        fc_geom_type, confidence, method = "Point", 1.0, "apd_block_family"
    else:
        fc_name, fc_geom_type, confidence, method = assign_fc(record["layer"], record["text"])
        if fc_name == "fc_misc":
            return None
        if fc_name == "IMB" and record["layer"].strip().casefold() != "home number":
            return None
        if fc_name == "CABLE" and record["object_name"] not in {
            "ACDBLINE", "ACDBLWPOLYLINE", "ACDBPOLYLINE", "ACDB2DPOLYLINE", "ACDB3DPOLYLINE",
        }:
            return None
        if fc_name in {"BOITE", "PTECH", "SITE"} and record["object_name"] != "ACDBPOINT":
            return None
    attrs = extract_attributes(record["text"], fc_name)
    for key, value in record["block_attributes"].items():
        if key in {"CODE", "REF_PM", "REF_SRO", "ORIGINE", "EXTREMITE", "TYPE", "TYPE_CABLE", "STATUT"}:
            attrs.setdefault(key, value)
    direct_label = str(attrs.get("CODE", "")).strip()
    polygon_class = fc_name in {"ZNRO", "ZPM"}
    endpoints_closed = len(points) >= 3 and (
        abs(points[0][0] - points[-1][0]) <= 1e-10
        and abs(points[0][1] - points[-1][1]) <= 1e-10
    )
    if polygon_class and len(points) < 3:
        return None
    if polygon_class and not (record["closed"] or endpoints_closed):
        return None
    is_closed = record["closed"] or (polygon_class and endpoints_closed)
    if is_closed and len(points) >= 3:
        wkt = _polygon_wkt(points)
    elif len(points) >= 2:
        wkt = _line_wkt(points)
    else:
        wkt = _point_wkt(cx, cy)
    return {
        "output_kind": "feature",
        "global_id": -1,
        "source_file": source_name,
        "layer": record["layer"],
        "layout": record["layout"],
        "cad_role": record["cad_role"],
        "cad_handle": record["handle"],
        "dwg_type": -1,
        "dwg_type_name": record["dwg_type_name"],
        "wkt": wkt,
        "points": points,
        "native_points": list(record["points"]),
        "native_centroid": record["centroid"],
        "centroid": (cx, cy),
        "is_closed": is_closed,
        "fc_name": fc_name,
        "fc_geom_type": fc_geom_type,
        "classification_confidence": confidence,
        "classification_method": method,
        "text": record["text"],
        "annotation_text": "",
        "display_label": direct_label,
        "label_method": "DWG_DIRECT" if direct_label else "UNAVAILABLE",
        "attrs": attrs,
        "is_insert_node": is_insert,
        "block_name": record["block_name"],
        "aci_color": record["aci_color"],
        "true_color": record["true_color"],
        "linetype": record["linetype"],
        "lineweight": record["lineweight"],
        "rotation": record["rotation"],
        "geographic_outlier": False,
    }


def build_items_from_records(records, source_name, reproject_point, assign_fc, classify_block, extract_attributes):
    """Turn neutral COM records into GIS features and non-spatial evidence."""
    items, annotations = [], []
    for record in records:
        role = record["cad_role"]
        source_evidence = _evidence_item(record, source_name, "source_evidence")
        items.append(source_evidence)
        if role in {"topology", "splicing"}:
            source_evidence["terminal_disposition"] = "annotation"
            items.append(_evidence_item(record, source_name, "topology_evidence"))
            continue
        if role == "style_legend":
            source_evidence["terminal_disposition"] = "legend"
            items.append(_evidence_item(record, source_name, "style_evidence"))
            continue
        if role == "design_summary":
            source_evidence["terminal_disposition"] = "annotation"
            if record["text"]:
                items.append(_evidence_item(record, source_name, "summary_evidence"))
            continue
        if role not in {"model", "plan"}:
            source_evidence["terminal_disposition"] = "out_of_scope"
            continue
        if record["object_name"] == "ACDBDIMENSION":
            source_evidence["terminal_disposition"] = "annotation"
            items.append(_evidence_item(record, source_name, "dimension_evidence"))
            continue
        if record["object_name"] in _TEXT_OBJECTS:
            fc_name, _, _, _ = assign_fc(record["layer"], record["text"])
            if fc_name == "IMB" and record["layer"].strip().casefold() == "home number":
                feature = _feature_item(
                    record, source_name, reproject_point, assign_fc, classify_block, extract_attributes,
                )
                if feature is not None:
                    code = record["text"].strip()
                    if code:
                        feature["attrs"].setdefault("CODE", code)
                        feature["code_source"] = "dwg_text"
                        feature["display_label"] = code
                        feature["label_method"] = "DWG_DIRECT"
                    items.append(feature)
                    source_evidence["terminal_disposition"] = "mapped"
                continue
            point = reproject_point(*record["centroid"])
            if -180 <= point[0] <= 180 and -90 <= point[1] <= 90:
                annotations.append({
                    "text": record["text"],
                    "centroid": point,
                    "native_centroid": record["centroid"],
                    "attrs": extract_attributes(record["text"], None),
                    "layer": record["layer"],
                    "layout": record["layout"],
                })
            source_evidence["terminal_disposition"] = "annotation"
            continue
        feature = _feature_item(record, source_name, reproject_point, assign_fc, classify_block, extract_attributes)
        if feature is not None:
            items.append(feature)
            source_evidence["terminal_disposition"] = "mapped"
        else:
            source_evidence["terminal_disposition"] = "graphic_only"

    features = [item for item in items if item.get("output_kind") == "feature"]
    leftovers = link_apd_annotations(annotations, features, sigma_native=15.0)
    for annotation in leftovers:
        ax, ay = annotation["centroid"]
        items.append({
            "output_kind": "annotation_evidence",
            "source_file": source_name,
            "layout": annotation["layout"],
            "cad_role": "plan_annotation",
            "handle": "",
            "layer": annotation["layer"],
            "dwg_type_name": "TEXT",
            "text": annotation["text"],
            "block_name": "",
            "aci_color": 256,
            "true_color": "",
            "linetype": "ByLayer",
            "lineweight": -1,
            "rotation": 0.0,
        })
    return items


def _bind_entity_keys(items, source):
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    for item in items:
        handle = str(item.get("handle", item.get("cad_handle", "")))
        layout = str(item.get("layout", ""))
        if handle:
            item["entity_key"] = hashlib.sha256(
                f"{source_hash}|{handle}|{layout}".encode("utf-8")
            ).hexdigest()
        item["source_sha256"] = source_hash
    return items


def _collect_records(database, assign_fc=None):
    collections = [("Model", "model", database.ModelSpace)]
    try:
        layouts = _safe_get(database, "Layouts")
        for layout in _iter_com_collection(layouts):
            name = str(_safe_get(layout, "Name", ""))
            if name.casefold() == "model":
                continue
            collections.append((name, classify_layout_role(name), _safe_get(layout, "Block")))
    except Exception:
        pass
    grouped = []
    for layout_name, layout_role, collection in collections:
        records = []
        if collection is None:
            continue
        selected_collections = (
            _select_model_collections(database, assign_fc)
            if layout_role == "model" and assign_fc is not None else []
        )
        entity_collections = selected_collections or [collection]
        seen_handles = set()
        for entity_collection in entity_collections:
          for entity in _iter_com_collection(entity_collection):
            object_name = str(_safe_get(entity, "ObjectName", "")).upper()
            layer_name = str(_safe_get(entity, "Layer", "0"))
            block_name = ""
            if layout_role == "model" and assign_fc is not None:
                keep = object_name in _TEXT_OBJECTS
                if object_name in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}:
                    block_name = str(
                        _safe_get(entity, "EffectiveName", _safe_get(entity, "Name", ""))
                    )
                    keep = is_telecom_block(block_name)
                elif not keep:
                    fc_name, _, _, _ = assign_fc(layer_name, "")
                    keep = fc_name != "fc_misc"
                if not keep:
                    continue
            handle = str(_safe_get(entity, "Handle", ""))
            if handle and handle in seen_handles:
                continue
            seen_handles.add(handle)
            record = extract_com_entity(
                entity, layout_name, layout_role,
                object_name_hint=object_name,
                layer_hint=layer_name,
                block_name_hint=block_name,
            )
            if record is not None:
                records.append(record)
        for selection in selected_collections:
            try:
                _retry_com(selection.Delete)
            except Exception:
                pass
        if layout_role in {"model", "plan"}:
            partition_plan_roles(records)
            if layout_role == "model":
                partition_model_legend(records)
        grouped.append((layout_name, layout_role, records))
    return grouped


def _open_autocad_database(dwg_path):
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    application = None
    created_application = False
    database = None
    opened_document = None
    last_error = None
    for progid in AUTOCAD_PROGIDS:
        try:
            application = win32com.client.GetActiveObject(progid)
            break
        except Exception:
            continue
    if application is None:
        for progid in AUTOCAD_PROGIDS:
            try:
                application = win32com.client.DispatchEx(progid)
                created_application = True
                break
            except Exception as exc:
                last_error = exc
    if application is None:
        pythoncom.CoUninitialize()
        raise RuntimeError(f"AutoCAD COM server is unavailable: {last_error}")
    try:
        try:
            application.Visible = False
        except Exception:
            pass
        target = str(Path(dwg_path).resolve()).casefold()
        documents = _safe_get(application, "Documents")
        try:
            for document in _iter_com_collection(documents):
                if str(_safe_get(document, "FullName", "")).casefold() == target:
                    database = document
                    break
        except Exception:
            pass
        if database is None:
            try:
                _retry_com(lambda: documents.Open(str(Path(dwg_path).resolve()), True))
                for document in _iter_com_collection(documents):
                    if str(_safe_get(document, "FullName", "")).casefold() == target:
                        database = document
                        opened_document = document
                        break
                if database is None:
                    database = _safe_get(application, "ActiveDocument")
                    opened_document = database
            except Exception as exc:
                last_error = exc
                database = None
        if database is None:
            database = application.GetInterfaceObject(OBJECTDBX_PROGID)
            _retry_com(lambda: database.Open(str(Path(dwg_path).resolve())))
        return pythoncom, application, created_application, database, opened_document
    except Exception:
        if created_application:
            try:
                _retry_com(application.Quit)
            except Exception:
                pass
        pythoncom.CoUninitialize()
        raise RuntimeError(f"AutoCAD could not open the DWG database read-only: {last_error}")


def _items_from_grouped(
    grouped, source, reproject_point, assign_fc, classify_block, extract_attributes,
):
    model_has_entities = False
    for _, role, records in grouped:
        if role != "model":
            continue
        for record in records:
            if record["cad_role"] != "model" or record["object_name"] in _TEXT_OBJECTS:
                continue
            if is_telecom_block(record.get("block_name", "")):
                model_has_entities = True
                break
            fc_name, _, _, _ = assign_fc(record.get("layer", ""), record.get("text", ""))
            if fc_name != "fc_misc":
                model_has_entities = True
                break
        if model_has_entities:
            break

    selected = []
    for _, role, records in grouped:
        if role == "plan" and model_has_entities:
            # Paper layouts repeat the model through viewports.  Their legend
            # and summary remain evidence, but plan geometry is not duplicated.
            for record in records:
                if record["cad_role"] == "plan":
                    record["cad_role"] = "layout"
        selected.extend(records)
    return build_items_from_records(
        selected, source.name, reproject_point, assign_fc, classify_block, extract_attributes,
    )


def read_dwg_with_autocad(dwg_path, reproject_point, assign_fc, classify_block, extract_attributes):
    """Read a DWG directly through AutoCAD; never export or create a DXF."""
    if os.name != "nt":
        raise RuntimeError("Direct AutoCAD DWG reading requires Windows")
    source = Path(dwg_path).resolve()
    if source.suffix.casefold() != ".dwg":
        raise ValueError("The direct AutoCAD reader accepts DWG input only")
    try:
        grouped = _extract_records_with_core_console(source)
        items = _items_from_grouped(
            grouped, source, reproject_point, assign_fc, classify_block, extract_attributes,
        )
        return _bind_entity_keys(items, source)
    except Exception as bulk_error:
        print(f"  AutoCAD bulk database extraction unavailable; COM fallback: {bulk_error}")

    pythoncom, application, created, database, opened_document = _open_autocad_database(source)
    try:
        grouped = _collect_records(database, assign_fc=assign_fc)
        items = _items_from_grouped(
            grouped, source, reproject_point, assign_fc, classify_block, extract_attributes,
        )
        return _bind_entity_keys(items, source)
    finally:
        if opened_document is not None:
            try:
                _retry_com(lambda: opened_document.Close(False))
            except Exception:
                pass
        database = None
        if created:
            try:
                _retry_com(application.Quit)
            except Exception:
                pass
        application = None
        pythoncom.CoUninitialize()


def extract_dwg_records(dwg_path):
    """Return the complete direct-DWG record stream without GIS semantics.

    This is the architecture boundary used by the v3 pipeline: ingestion owns
    only immutable CAD facts. Role assignment, classification, topology,
    georeferencing, and delivery happen in later stages and cannot influence
    which database records are extracted.
    """
    if os.name != "nt":
        raise RuntimeError("Direct AutoCAD DWG reading requires Windows")
    source = Path(dwg_path).resolve()
    if source.suffix.casefold() != ".dwg":
        raise ValueError("The direct AutoCAD reader accepts DWG input only")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        grouped = _extract_records_with_core_console(source)
    except Exception as bulk_error:
        print(f"  AutoCAD bulk database extraction unavailable; COM fallback: {bulk_error}")
        pythoncom, application, created, database, opened_document = _open_autocad_database(source)
        try:
            grouped = _collect_records(database, assign_fc=None)
        finally:
            if opened_document is not None:
                try:
                    _retry_com(lambda: opened_document.Close(False))
                except Exception:
                    pass
            database = None
            if created:
                try:
                    _retry_com(application.Quit)
                except Exception:
                    pass
            application = None
            pythoncom.CoUninitialize()

    records = []
    for _, _, grouped_records in grouped:
        for record in grouped_records:
            item = dict(record)
            handle = str(item.get("handle", ""))
            layout = str(item.get("layout", ""))
            identity = f"{source_hash}|{handle}|{layout}"
            if not handle:
                identity += f"|{item.get('dwg_type_name', '')}|{item.get('text', '')}"
            item["entity_key"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            item["source_sha256"] = source_hash
            item["source_file"] = source.name
            records.append(item)
    return records
