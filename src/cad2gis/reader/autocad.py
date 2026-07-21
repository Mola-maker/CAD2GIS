"""Direct, read-only AutoCAD DWG ingestion for the experiment pipeline.

The reader deliberately does not create DXF files.  On Windows it discovers
an installed AutoCAD Core Console (or accepts an explicit executable), opens
the DWG read-only, and inventories model/layout/block-definition objects.
"""

from __future__ import annotations

import math
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from apd_rules import is_telecom_block, link_apd_annotations
from cad2gis_v3.model import (
    CURVE_FACTS_SCHEMA,
    canonical_curve_facts,
    canonical_curve_fingerprint,
)


AUTOCAD_PROGIDS = ("AutoCAD.Application.26", "AutoCAD.Application")
OBJECTDBX_PROGID = "ObjectDBX.AxDbDocument.26"
DEFAULT_ACCORECONSOLE = Path("C:/Program Files/Autodesk/AutoCAD 2027/accoreconsole.exe")
DEFAULT_ACCORECONSOLE_TIMEOUT = 120.0
ACCORECONSOLE_ENV = "CAD2GIS_ACCORECONSOLE"
ACCORECONSOLE_TIMEOUT_ENV = "CAD2GIS_ACCORECONSOLE_TIMEOUT"
COM_FALLBACK_ENV = "CAD2GIS_ALLOW_COM_FALLBACK"
BULK_POLICY_STRICT = "strict"
BULK_POLICY_SKIP_MALFORMED = "skip_malformed_rows"
BULK_PROTOCOL_SCHEMA = "cad2gis-autocad-bulk-tsv-v3"
_BULK_COMPATIBILITY_POLICIES = {
    BULK_POLICY_STRICT,
    BULK_POLICY_SKIP_MALFORMED,
}
_AUTOCAD_VERSION_DIRECTORY = re.compile(r"(?i)^AutoCAD\s+(\d{4})$")


class BulkProtocolError(ValueError):
    """One malformed row at the authoritative bulk reader boundary."""

    def __init__(self, message, *, line_number=None, field_name="row"):
        self.line_number = line_number
        self.field_name = str(field_name)
        location = f"bulk row {line_number}" if line_number is not None else "bulk row"
        super().__init__(f"{location}, field {self.field_name}: {message}")


class BulkProtocolViolation(RuntimeError):
    """A strict protocol failure that is never eligible for COM fallback."""


class BulkExtractionResult(list):
    """List-compatible grouped records with non-lossy parser diagnostics."""

    def __init__(self, values=(), *, diagnostics=None):
        super().__init__(values)
        self.diagnostics = dict(diagnostics or {})


class DWGRecordInventory(list):
    """Flat record inventory with reader-protocol diagnostics attached.

    The object intentionally remains list-compatible for the existing ingest
    boundary.  Callers that opt into the lossy compatibility policy can read
    ``diagnostics`` and must not mistake a partially parsed stream for a
    complete inventory.
    """

    def __init__(self, values=(), *, diagnostics=None):
        super().__init__(values)
        self.diagnostics = dict(diagnostics or {})


def _validate_bulk_compatibility_policy(policy):
    if policy is None:
        return BULK_POLICY_STRICT
    if not isinstance(policy, str) or policy not in _BULK_COMPATIBILITY_POLICIES:
        choices = ", ".join(sorted(_BULK_COMPATIBILITY_POLICIES))
        raise ValueError(
            f"Invalid bulk compatibility policy {policy!r}; expected one of: {choices}"
        )
    return policy


def _resolve_accoreconsole_timeout(timeout=None, *, environ=None):
    """Resolve a positive finite subprocess timeout and its configuration source."""
    environ = os.environ if environ is None else environ
    if timeout is not None:
        value, source = timeout, "explicit"
    elif ACCORECONSOLE_TIMEOUT_ENV in environ:
        value, source = environ[ACCORECONSOLE_TIMEOUT_ENV], "environment"
    else:
        value, source = DEFAULT_ACCORECONSOLE_TIMEOUT, "default"
    if isinstance(value, bool):
        raise ValueError("AutoCAD Core Console timeout must be a positive finite number")
    if isinstance(value, str) and not value.strip():
        raise ValueError("AutoCAD Core Console timeout cannot be empty")
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"AutoCAD Core Console timeout must be a positive finite number, got {value!r}"
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(
            f"AutoCAD Core Console timeout must be a positive finite number, got {value!r}"
        )
    return seconds, source


def _autocad_version(path):
    match = _AUTOCAD_VERSION_DIRECTORY.fullmatch(path.parent.name)
    return int(match.group(1)) if match else None


def _discover_accoreconsole_paths(*, environ=None):
    """Discover installed versioned Core Console binaries without launching them."""
    environ = os.environ if environ is None else environ
    roots = []
    for name in ("ProgramW6432", "ProgramFiles", "PROGRAMFILES", "ProgramFiles(x86)"):
        value = environ.get(name)
        if value and str(value).strip():
            roots.append(Path(value))
    # Preserve the historical 2027 location as a candidate even when the
    # process environment omits ProgramFiles (common in constrained runners).
    roots.append(DEFAULT_ACCORECONSOLE.parents[2])

    versioned = []
    seen = set()
    for root in roots:
        autodesk_root = root / "Autodesk"
        try:
            candidates = autodesk_root.glob("AutoCAD */accoreconsole.exe")
            for candidate in candidates:
                key = str(candidate).casefold()
                if key in seen or not candidate.is_file():
                    continue
                seen.add(key)
                versioned.append(candidate)
        except OSError:
            continue
    versioned.sort(
        key=lambda path: (_autocad_version(path) or -1, str(path).casefold()),
        reverse=True,
    )

    path_binary = shutil.which("accoreconsole.exe") or shutil.which("accoreconsole")
    if path_binary:
        candidate = Path(path_binary)
        key = str(candidate).casefold()
        if key not in seen and candidate.is_file():
            versioned.append(candidate)
    return versioned


def _configured_accoreconsole_path(accoreconsole=None, *, environ=None):
    """Resolve explicit, environment, or newest installed Core Console path."""
    environ = os.environ if environ is None else environ
    if accoreconsole is not None:
        value, source = accoreconsole, "explicit"
    elif ACCORECONSOLE_ENV in environ:
        value, source = environ[ACCORECONSOLE_ENV], "environment"
    else:
        discovered = _discover_accoreconsole_paths(environ=environ)
        if not discovered:
            raise RuntimeError(
                "AutoCAD Core Console was not found; pass accoreconsole explicitly, "
                f"set {ACCORECONSOLE_ENV}, or install a discoverable AutoCAD version "
                f"(the historical 2027 candidate is {DEFAULT_ACCORECONSOLE})"
            )
        path = discovered[0].resolve()
        source = "default_2027" if path == DEFAULT_ACCORECONSOLE.resolve() else "version_discovery"
        return path, source

    try:
        raw_path = os.fspath(value)
    except TypeError as exc:
        raise TypeError(
            "AutoCAD Core Console path must be a string or path-like value"
        ) from exc
    if not str(raw_path).strip():
        raise ValueError("AutoCAD Core Console path cannot be empty")
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise RuntimeError(f"AutoCAD Core Console not found: {path}")
    return path.resolve(), source


def preflight_autocad_reader(
    *, accoreconsole=None, timeout=None, compatibility_policy=BULK_POLICY_STRICT,
    environ=None,
):
    """Inspect direct-DWG runtime readiness without opening AutoCAD or a DWG."""
    environ = os.environ if environ is None else environ
    result = {
        "schema_version": "cad2gis-autocad-reader-preflight-v1",
        "read_only": True,
        "ok": False,
        "status": "not_ready",
        "platform": {"name": os.name, "ok": os.name == "nt"},
        "core_console": {
            "ok": False,
            "path": None,
            "source": None,
            "version": None,
        },
        "timeout": {"ok": False, "seconds": None, "source": None},
        "bulk_compatibility_policy": None,
        "com_fallback_enabled": _com_fallback_enabled(environ=environ),
        "errors": [],
        "warnings": [],
    }
    try:
        policy = _validate_bulk_compatibility_policy(compatibility_policy)
        result["bulk_compatibility_policy"] = policy
        if policy != BULK_POLICY_STRICT:
            result["warnings"].append(
                "Malformed bulk rows will be skipped and counted by explicit compatibility policy"
            )
    except (TypeError, ValueError) as exc:
        result["errors"].append(str(exc))

    try:
        seconds, source = _resolve_accoreconsole_timeout(timeout, environ=environ)
        result["timeout"].update(ok=True, seconds=seconds, source=source)
    except (TypeError, ValueError) as exc:
        result["errors"].append(str(exc))

    try:
        path, source = _configured_accoreconsole_path(
            accoreconsole, environ=environ,
        )
        result["core_console"].update(
            ok=True,
            path=str(path),
            source=source,
            version=_autocad_version(path),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        result["errors"].append(str(exc))

    if result["errors"]:
        result["status"] = "invalid_configuration"
    elif not result["platform"]["ok"]:
        result["status"] = "unsupported_platform"
    else:
        result["ok"] = True
        result["status"] = "ready"
    return result

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
      ((= code 92) "\\\\") ((= code 124) "\\p") ((= code 9) "\\t")
      ((= code 10) "\\n") ((= code 13) "\\r")
      (T (chr code)))))
    (setq index (1+ index))) result)
(defun c2g-num (value) (if value (rtos value 2 16) ""))
(defun c2g-point (point)
  (if point (strcat (c2g-num (car point)) "," (c2g-num (cadr point))) ""))
(defun c2g-add-point (result point)
  (if (= result "") (c2g-point point) (strcat result ";" (c2g-point point))))
(defun c2g-point3 (point / z)
  (if point
    (progn
      (setq z (if (caddr point) (caddr point) 0.0))
      (strcat (c2g-num (car point)) "," (c2g-num (cadr point)) "," (c2g-num z))) ""))
(defun c2g-add-point3 (result point)
  (if (= result "") (c2g-point3 point) (strcat result ";" (c2g-point3 point))))
(defun c2g-add-number (result value)
  (if (= result "") (c2g-num value) (strcat result "," (c2g-num value))))
(defun c2g-wcs-point (normal point / converted)
  (if point
    (progn
      (setq converted (vl-catch-all-apply 'trans (list point normal 0)))
      (if (or (vl-catch-all-error-p converted) (not converted))
        (if (equal normal '(0.0 0.0 1.0) 0.000000000001) point nil)
        converted)) nil))
(defun c2g-elevation (data kind / point)
  (cond
    ((= kind "LWPOLYLINE") (c2g-num (c2g-get 38 data 0.0)))
    ((= kind "POLYLINE")
      (setq point (c2g-get 10 data '(0.0 0.0 0.0)))
      (c2g-num (if (caddr point) (caddr point) 0.0)))
    (T "")))
(defun c2g-curve-vertices (entity data kind / result item next nextdata nextkind elevation point flags)
  (setq result "" elevation (c2g-get 38 data 0.0) flags (c2g-get 70 data 0))
  (cond
    ((= kind "LINE")
      (setq result (c2g-add-point3 result (c2g-get 10 data nil)))
      (setq result (c2g-add-point3 result (c2g-get 11 data nil))))
    ((= kind "LWPOLYLINE")
      (foreach item data
        (if (= (car item) 10)
          (progn
            (setq point (cdr item))
            (setq point (list (car point) (cadr point) elevation))
            (setq result (c2g-add-point3 result
              (c2g-wcs-point (c2g-get 210 data '(0.0 0.0 1.0)) point)))))))
    ((= kind "POLYLINE")
      (setq point (c2g-get 10 data '(0.0 0.0 0.0)))
      (setq elevation (if (caddr point) (caddr point) 0.0))
      (setq next (entnext entity))
      (while next
        (setq nextdata (entget next) nextkind (c2g-get 0 nextdata ""))
        (cond
          ((= nextkind "VERTEX")
            (setq point (c2g-get 10 nextdata nil))
            (if (and point (/= 8 (logand flags 8)))
              (setq point (list (car point) (cadr point) elevation)))
            (setq result (c2g-add-point3 result
              (if (= 8 (logand flags 8)) point
                (c2g-wcs-point (c2g-get 210 data '(0.0 0.0 1.0)) point)))))
          ((= nextkind "SEQEND") (setq next nil)))
        (if next (setq next (entnext next)))))
    ((= kind "SPLINE")
      (foreach item data
        (if (= (car item) 10)
          (setq result (c2g-add-point3 result (cdr item))))))) result)
(defun c2g-curve-bulges (entity data kind / result item pending next nextdata nextkind count)
  (setq result "" pending nil)
  (cond
    ((= kind "LINE")
      (setq result (c2g-add-number result 0.0))
      (setq result (c2g-add-number result 0.0)))
    ((= kind "LWPOLYLINE")
      (foreach item data
        (cond
          ((= (car item) 10)
            (if pending (setq result (c2g-add-number result 0.0)))
            (setq pending T))
          ((and pending (= (car item) 42))
            (setq result (c2g-add-number result (cdr item)) pending nil))))
      (if pending (setq result (c2g-add-number result 0.0))))
    ((= kind "POLYLINE")
      (setq next (entnext entity))
      (while next
        (setq nextdata (entget next) nextkind (c2g-get 0 nextdata ""))
        (cond
          ((= nextkind "VERTEX")
            (setq result (c2g-add-number result (c2g-get 42 nextdata 0.0))))
          ((= nextkind "SEQEND") (setq next nil)))
        (if next (setq next (entnext next)))))
    ((= kind "SPLINE")
      (setq count 0)
      (foreach item data (if (= (car item) 10) (setq count (1+ count))))
      (repeat count (setq result (c2g-add-number result 0.0))))) result)
(defun c2g-code-values (data code / result item)
  (setq result "")
  (foreach item data
    (if (= (car item) code) (setq result (c2g-add-number result (cdr item))))) result)
(defun c2g-code-points (entity data code / result item)
  (setq result "")
  (foreach item data
    (if (= (car item) code)
      (setq result (c2g-add-point3 result (cdr item))))) result)
(defun c2g-primitive (entity data kind / result center)
  (setq result "")
  (cond
    ((member kind '("CIRCLE" "ARC"))
      (setq center (c2g-point3
        (c2g-wcs-point (c2g-get 210 data '(0.0 0.0 1.0)) (c2g-get 10 data nil))))
      (setq result (strcat "center_wcs=" center "|radius=" (c2g-num (c2g-get 40 data 0.0))))
      (if (= kind "ARC")
        (setq result (strcat result "|start_angle=" (c2g-num (c2g-get 50 data 0.0))
          "|end_angle=" (c2g-num (c2g-get 51 data 0.0))))))
    ((= kind "ELLIPSE")
      (setq result (strcat
        "center_wcs=" (c2g-point3 (c2g-get 10 data nil))
        "|major_axis=" (c2g-point3 (c2g-get 11 data nil))
        "|radius_ratio=" (c2g-num (c2g-get 40 data 0.0))
        "|start_parameter=" (c2g-num (c2g-get 41 data 0.0))
        "|end_parameter=" (c2g-num (c2g-get 42 data 6.283185307179586)))))
    ((= kind "SPLINE")
      (setq result (strcat
        "degree=" (itoa (c2g-get 71 data 0))
        "|flags=" (itoa (c2g-get 70 data 0))
        "|knot_values=" (c2g-code-values data 40)
        "|weights=" (c2g-code-values data 41)
        "|fit_points_wcs=" (c2g-code-points entity data 11))))
    ((= kind "POLYLINE")
      (setq result (strcat "flags=" (itoa (c2g-get 70 data 0)))))) result)
(defun c2g-owner-handle (data / value)
  (setq value (c2g-get 330 data nil))
  (cond
    ((= (type value) 'ENAME) (c2g-get 5 (entget value) ""))
    ((= (type value) 'STR) value)
    (T "")))
(defun c2g-length (entity / endparam result)
  (setq endparam (vl-catch-all-apply 'vlax-curve-getEndParam (list entity)))
  (if (vl-catch-all-error-p endparam) ""
    (progn
      (setq result (vl-catch-all-apply 'vlax-curve-getDistAtParam (list entity endparam)))
      (if (vl-catch-all-error-p result) "" (c2g-num result)))))
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
(defun c2g-text (data kind / result item value)
  (setq result "")
  (cond
    ((= kind "MTEXT")
      (foreach item data
        (if (= (car item) 3) (setq result (strcat result (cdr item)))))
      (setq result (strcat result (c2g-get 1 data ""))))
    ((member kind '("MULTILEADER" "MLEADER"))
      (foreach item data
        (if (= (car item) 304)
          (progn
            (setq value (cdr item))
            (if (and value (/= value "")
                     (not (wcmatch (strcase value) "LEADER_LINE*")))
              (setq result (if (= result "") value (strcat result (chr 10) value)))))))
      (if (= result "") (setq result (c2g-get 1 data ""))))
    ((= kind "TABLE")
      (foreach item data
        (if (= (car item) 304)
          (progn
            (setq value (cdr item))
            (if (and value (/= value ""))
              (setq result (if (= result "") value (strcat result (chr 10) value)))))))
      (if (= result "") (setq result (c2g-get 1 data ""))))
    (T (setq result (c2g-get 1 data "")))) result)
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
  (wcmatch kind "LINE,LWPOLYLINE,POLYLINE,CIRCLE,ARC,SPLINE,ELLIPSE,POINT,INSERT,TEXT,MTEXT,ATTRIB,ATTDEF,MULTILEADER,MLEADER,TABLE,DIMENSION"))
(defun c2g-add-reason (result reason)
  (cond
    ((or (not reason) (= reason "")) result)
    ((= result "") reason)
    (T (strcat result ";" reason))))
(defun c2g-xref-status (flags path)
  (cond
    ((= 8 (logand flags 8)) "xref_overlay")
    ((= 4 (logand flags 4)) "xref")
    ((= 16 (logand flags 16)) "external_dependent")
    ((and path (/= path "")) "external_reference")
    (T "not_external")))
(defun c2g-write-entity (file entity layoutoverride containerblock / data kind handle owner layer layout color truecolor linetype lineweight rotation closed block text textsource attrs points radius start end row flags layerdata layercolor layertruecolor layerlinetype layerlineweight scalex scaley scalez dimoverride dynamicprops unsupported nativelength curveschema curvevertices curvebulges elevation normal extrusion primitive blockdata blockbase blockbasestatus blockhandle blockflags xrefpath xrefstatus insertpoint insertpointstatus insertnormal insertnormalstatus insertextrusion insertextrusionstatus nestingcontext geostatus supportstatus supportedp)
  (setq data (entget entity) kind (c2g-get 0 data ""))
  (if (/= kind "")
    (progn
      (setq handle (c2g-get 5 data "") owner (c2g-owner-handle data) layer (c2g-get 8 data "0"))
      (setq layout (if layoutoverride layoutoverride (c2g-get 410 data "Model")))
      (setq color (c2g-get 62 data 256) truecolor (c2g-get 420 data -1))
      (setq linetype (c2g-get 6 data "ByLayer") lineweight (c2g-get 370 data -1) rotation (c2g-get 50 data 0.0))
      (setq layerdata (tblsearch "LAYER" layer))
      (setq layercolor (abs (c2g-get 62 layerdata 7)) layertruecolor (c2g-get 420 layerdata -1))
      (setq layerlinetype (c2g-get 6 layerdata "Continuous") layerlineweight (c2g-get 370 layerdata -1))
      (setq flags (c2g-get 70 data 0) closed (if (or (= kind "CIRCLE") (= 1 (logand flags 1))) 1 0))
      (setq block (if (= kind "INSERT") (c2g-get 2 data "") ""))
      (setq blockdata (if (= kind "INSERT") (tblsearch "BLOCK" block) nil))
      (setq blockbase (if blockdata (c2g-get 10 blockdata nil) nil))
      (setq blockbasestatus (if (= kind "INSERT")
        (if blockbase "available" "unavailable") "not_applicable"))
      (setq blockhandle (if blockdata (c2g-get 5 blockdata "") ""))
      (setq blockflags (if blockdata (c2g-get 70 blockdata 0) 0))
      (setq xrefpath (if blockdata (c2g-get 1 blockdata "") ""))
      (setq xrefstatus (if (= kind "INSERT")
        (c2g-xref-status blockflags xrefpath) "not_external"))
      (setq insertnormal (if (= kind "INSERT") (c2g-get 210 data nil) nil))
      (setq insertextrusion insertnormal)
      (setq insertnormalstatus (if (= kind "INSERT")
        (if insertnormal "available" "unavailable") "not_applicable"))
      (setq insertextrusionstatus insertnormalstatus)
      (setq insertpoint (if (= kind "INSERT")
        (c2g-wcs-point insertnormal (c2g-get 10 data nil)) nil))
      (setq insertpointstatus (if (= kind "INSERT")
        (if insertpoint "available" "unavailable") "not_applicable"))
      (setq nestingcontext (if (and containerblock (/= containerblock ""))
        "block_definition" "drawing_space"))
      (setq scalex (if (= kind "INSERT") (c2g-get 41 data 1.0) 1.0))
      (setq scaley (if (= kind "INSERT") (c2g-get 42 data 1.0) 1.0))
      (setq scalez (if (= kind "INSERT") (c2g-get 43 data 1.0) 1.0))
      (setq text (if (member kind '("TEXT" "MTEXT" "ATTRIB" "ATTDEF" "MULTILEADER" "MLEADER" "TABLE" "DIMENSION")) (c2g-text data kind) ""))
      (setq attrs (if (= kind "INSERT") (c2g-attributes entity) ""))
      (setq textsource (cond
        ((member kind '("TEXT" "MTEXT")) "entity_text")
        ((member kind '("ATTRIB" "ATTDEF")) "attribute_text")
        ((member kind '("MULTILEADER" "MLEADER")) "multileader_text")
        ((= kind "TABLE") "table_cells")
        ((= kind "DIMENSION") "dimension_text_override")
        ((and (= kind "INSERT") (/= attrs "")) "block_attributes")
        (T "")))
      (setq dimoverride (if (= kind "DIMENSION") (c2g-get 1 data "") ""))
      (setq dynamicprops "")
      (setq nativelength (if (member kind '("LINE" "LWPOLYLINE" "POLYLINE" "CIRCLE" "ARC" "SPLINE" "ELLIPSE")) (c2g-length entity) ""))
      (setq curveschema (if (member kind '("LINE" "LWPOLYLINE" "POLYLINE" "CIRCLE" "ARC" "SPLINE" "ELLIPSE")) "cad2gis-curve-facts-v1" ""))
      (setq curvevertices (if (/= curveschema "") (c2g-curve-vertices entity data kind) ""))
      (setq curvebulges (if (/= curveschema "") (c2g-curve-bulges entity data kind) ""))
      (setq elevation (if (/= curveschema "") (c2g-elevation data kind) ""))
      (setq normal (if (/= curveschema "") (c2g-point3 (c2g-get 210 data '(0.0 0.0 1.0))) ""))
      (setq extrusion normal)
      (setq primitive (if (/= curveschema "") (c2g-primitive entity data kind) ""))
      (setq supportedp (c2g-supported kind))
      (setq unsupported "")
      (if (= kind "INSERT")
        (setq unsupported (c2g-add-reason unsupported "dynamic_block_properties_unavailable_in_bulk_backend")))
      (if (and (member kind '("MULTILEADER" "MLEADER")) (= text ""))
        (setq unsupported (c2g-add-reason unsupported "multileader_text_unavailable_in_bulk_backend")))
      (if (and (= kind "TABLE") (= text ""))
        (setq unsupported (c2g-add-reason unsupported "table_text_unavailable_in_bulk_backend")))
      (if (not supportedp)
        (setq unsupported (c2g-add-reason unsupported "geometry_unsupported_in_bulk_backend")))
      (if (/= xrefstatus "not_external")
        (setq unsupported (c2g-add-reason unsupported "external_reference_geometry_not_embedded")))
      (setq points (if supportedp (c2g-points entity data kind) ""))
      (setq geostatus (cond
        ((not supportedp) "unavailable")
        ((= kind "INSERT") (if insertpoint "anchor_only" "unavailable"))
        ((= points "") "unavailable")
        (T "available")))
      (setq supportstatus (if (or (not supportedp) (/= xrefstatus "not_external"))
        "inventory_only" "supported"))
      (setq radius (if (= kind "DIMENSION") (c2g-get 42 data 0.0) (c2g-get 40 data 0.0)))
      (setq start (c2g-get 50 data 0.0) end (c2g-get 51 data 6.283185307179586))
      (setq row (strcat
        (c2g-escape kind) (chr 9) (c2g-escape handle) (chr 9) (c2g-escape layer) (chr 9)
        (c2g-escape layout) (chr 9) (itoa color) (chr 9) (itoa truecolor) (chr 9)
        (c2g-escape linetype) (chr 9) (itoa lineweight) (chr 9) (c2g-num rotation) (chr 9)
        (itoa closed) (chr 9) (c2g-escape block) (chr 9) (c2g-escape text) (chr 9)
        attrs (chr 9) points (chr 9) (c2g-num radius) (chr 9) (c2g-num start) (chr 9) (c2g-num end) (chr 9)
        (itoa layercolor) (chr 9) (itoa layertruecolor) (chr 9) (c2g-escape layerlinetype) (chr 9) (itoa layerlineweight) (chr 9)
        (c2g-num scalex) (chr 9) (c2g-num scaley) (chr 9) (c2g-num scalez) (chr 9)
        (c2g-escape owner) (chr 9) (c2g-escape textsource) (chr 9) (c2g-escape dimoverride) (chr 9)
        dynamicprops (chr 9) (c2g-escape unsupported) (chr 9) nativelength (chr 9)
        curveschema (chr 9) curvevertices (chr 9) curvebulges (chr 9) elevation (chr 9)
        normal (chr 9) extrusion (chr 9) kind (chr 9) (c2g-escape primitive) (chr 9)
        (c2g-point3 insertpoint) (chr 9) insertpointstatus (chr 9)
        (c2g-point3 blockbase) (chr 9) blockbasestatus (chr 9)
        (c2g-point3 insertnormal) (chr 9) insertnormalstatus (chr 9)
        (c2g-point3 insertextrusion) (chr 9) insertextrusionstatus (chr 9)
        (c2g-escape (if containerblock containerblock "")) (chr 9) nestingcontext (chr 9)
        (c2g-escape blockhandle) (chr 9) (if blockdata (itoa blockflags) "") (chr 9)
        (c2g-escape xrefpath) (chr 9) xrefstatus (chr 9) geostatus (chr 9) supportstatus))
      (write-line row file))))
(defun c2g-write-block-record (file blockdata blockname / handle owner base basestatus flags path xrefstatus unsupported row)
  (setq handle (c2g-get 5 blockdata ""))
  (setq owner (c2g-owner-handle blockdata))
  (setq base (c2g-get 10 blockdata nil))
  (setq basestatus (if base "available" "unavailable"))
  (setq flags (c2g-get 70 blockdata 0))
  (setq path (c2g-get 1 blockdata ""))
  (setq xrefstatus (c2g-xref-status flags path))
  (setq unsupported "geometry_unavailable_for_block_definition_record")
  (if (/= xrefstatus "not_external")
    (setq unsupported (c2g-add-reason unsupported "external_reference_geometry_not_embedded")))
  (setq row (strcat
    "BLOCK_RECORD" (chr 9) (c2g-escape handle) (chr 9) "0" (chr 9)
    (c2g-escape (strcat "BLOCKDEF:" blockname)) (chr 9)
    "256" (chr 9) "-1" (chr 9) "ByLayer" (chr 9) "-1" (chr 9)
    "0" (chr 9) "0" (chr 9) (c2g-escape blockname) (chr 9) "" (chr 9)
    "" (chr 9) "" (chr 9) "0" (chr 9) "0" (chr 9) "0" (chr 9)
    "7" (chr 9) "-1" (chr 9) "Continuous" (chr 9) "-1" (chr 9)
    "1" (chr 9) "1" (chr 9) "1" (chr 9) (c2g-escape owner) (chr 9)
    "" (chr 9) "" (chr 9) "" (chr 9) (c2g-escape unsupported) (chr 9) "" (chr 9)
    "" (chr 9) "" (chr 9) "" (chr 9) "" (chr 9) "" (chr 9) "" (chr 9) "" (chr 9) "" (chr 9)
    "" (chr 9) "not_applicable" (chr 9)
    (c2g-point3 base) (chr 9) basestatus (chr 9)
    "" (chr 9) "not_applicable" (chr 9) "" (chr 9) "not_applicable" (chr 9)
    "" (chr 9) "block_definition_record" (chr 9) (c2g-escape handle) (chr 9)
    (itoa flags) (chr 9) (c2g-escape path) (chr 9) xrefstatus (chr 9)
    "unavailable" (chr 9) "inventory_only"))
  (write-line row file))
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
        (c2g-write-entity file (ssname selection index) nil nil)
        (setq index (1+ index)))))
  (setq blockdata (tblnext "BLOCK" T))
  (while blockdata
    (setq blockname (c2g-get 2 blockdata ""))
    (if (and (/= (strcase blockname) "*MODEL_SPACE") (not (wcmatch (strcase blockname) "*PAPER_SPACE*")))
      (progn
        (c2g-write-block-record file blockdata blockname)
        (setq blockentity (entnext (tblobjname "BLOCK" blockname)))
        (while blockentity
          (setq blockkind (c2g-get 0 (entget blockentity) ""))
          (if (= blockkind "ENDBLK")
            (setq blockentity nil)
            (progn
              (c2g-write-entity file blockentity (strcat "BLOCKDEF:" blockname) blockname)
              (setq blockentity (entnext blockentity)))))))
    (setq blockdata (tblnext "BLOCK")))
  (close file) (princ))
'''

_TEXT_OBJECTS = {
    "ACDBTEXT", "ACDBMTEXT", "ACDBATTRIBUTE", "ACDBATTRIBUTEREFERENCE",
    "ACDBATTRIBUTEDEFINITION", "ACDBMLEADER", "ACDBMULTILEADER", "ACDBTABLE",
}
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
RAW_PROPERTIES_SCHEMA = "cad2gis-raw-properties-v1"
_CURVE_DWG_TYPES = {
    "LINE", "LWPOLYLINE", "POLYLINE", "2DPOLYLINE", "3DPOLYLINE",
    "CIRCLE", "ARC", "SPLINE", "ELLIPSE",
}
# COM exposes a subtly different curve surface from the authoritative bulk
# extractor.  Only these objects can become a reviewed CABLE route; their
# curve facts therefore need a strict, loss-aware read when COM is the active
# backend.  Other curve/non-curve records retain the historical permissive
# COM behaviour because they are not consumed by the CABLE geometry gate.
_COM_ROUTE_CURVE_OBJECTS = {
    "ACDBPOLYLINE", "ACDBLWPOLYLINE", "ACDB2DPOLYLINE",
}
_COM_CURVE_OBJECTS = {
    "ACDBLINE", "ACDBPOLYLINE", "ACDBLWPOLYLINE", "ACDB2DPOLYLINE",
    "ACDB3DPOLYLINE", "ACDBSPLINE", "ACDBCIRCLE", "ACDBARC", "ACDBELLIPSE",
}
_COM_SUPPORTED_OBJECTS = _COM_CURVE_OBJECTS | _TEXT_OBJECTS | {
    "ACDBPOINT", "ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE",
    "ACDBTABLE", "ACDBMLEADER", "ACDBMULTILEADER", "ACDBLEADER",
}
_BULK_ROW_LENGTHS = {17, 21, 24, 29, 30, 38, 54}
_INSTANCE_FACTS_ROW_LENGTH = 54
_INSTANCE_STATUS_VALUES = {
    "available", "unavailable", "not_applicable", "legacy_unavailable",
}
_NESTING_CONTEXT_VALUES = {
    "drawing_space", "block_definition", "block_definition_record", "unknown",
}
_EXTERNAL_REFERENCE_STATUS_VALUES = {
    "not_external", "xref", "xref_overlay", "external_dependent",
    "external_reference", "unknown", "legacy_unknown",
}
_GEOMETRY_STATUS_VALUES = {
    "available", "anchor_only", "unavailable", "partial", "unknown",
}
_INVENTORY_SUPPORT_VALUES = {
    "supported", "inventory_only", "legacy", "unknown",
}


def _com_layer_is_cable(layer_name):
    """Recognize CABLE-named layers without importing GIS classification rules."""
    return bool(re.search(r"(?i)(?<![A-Z0-9])CABLE(?![A-Z0-9])", str(layer_name)))


def _com_fallback_enabled(*, environ=None):
    environ = os.environ if environ is None else environ
    return environ.get(COM_FALLBACK_ENV, "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _authorize_com_fallback(bulk_error):
    """Fail closed unless the semantically different COM backend is explicit."""
    if isinstance(bulk_error, BulkProtocolViolation):
        raise RuntimeError(
            "AutoCAD Core Console returned a malformed inventory; strict bulk "
            "protocol violations are not eligible for COM fallback"
        ) from bulk_error
    if not _com_fallback_enabled():
        raise RuntimeError(
            "AutoCAD Core Console inventory failed and the non-equivalent COM "
            f"fallback is disabled; set {COM_FALLBACK_ENV}=1 to opt in explicitly"
        ) from bulk_error
    print(
        "  AutoCAD bulk database extraction unavailable; explicitly enabled "
        f"COM fallback: {bulk_error}"
    )


def _canonical_json_value(value):
    """Return a deterministic, JSON-safe representation of an AutoCAD value."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    try:
        return [_canonical_json_value(item) for item in value]
    except Exception:
        # COM VARIANT values are not guaranteed to be iterable even when they
        # expose an iterator-like interface.  Never let an opaque automation
        # object leak past the reader boundary or make the inventory fail.
        try:
            return str(value)
        except Exception:
            return f"<{type(value).__name__}>"


def _float_or_none(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _canonical_raw_properties(
    record,
    *,
    extraction_backend,
    reader_backend_status,
    owner_handle="",
    raw_text=None,
    text_source="",
    dimension_text_override="",
    dynamic_block_properties=None,
    dynamic_block_properties_status="not_applicable",
    block_effective_name="",
    block_reference_name="",
    native_length_source="",
    curve_facts=None,
    curve_fingerprint="",
    insertion_point_wcs=None,
    insertion_point_status="not_applicable",
    block_base_point=None,
    block_base_point_status="not_applicable",
    insert_normal=None,
    insert_normal_status="not_applicable",
    insert_extrusion=None,
    insert_extrusion_status="not_applicable",
    insert_scale=None,
    insert_scale_status="not_applicable",
    insert_rotation=None,
    insert_rotation_status="not_applicable",
    container_block_name="",
    nesting_context="drawing_space",
    block_definition_handle="",
    block_flags=None,
    external_reference_path="",
    external_reference_status="not_external",
    geometry_status="unavailable",
    inventory_support_status="inventory_only",
    unsupported_reasons=(),
):
    """Build the fixed raw-fact schema shared by both extraction backends."""
    attributes = {
        str(key).strip().upper(): str(value)
        for key, value in dict(record.get("block_attributes") or {}).items()
        if str(key).strip()
    }
    reasons = sorted({str(item).strip() for item in unsupported_reasons if str(item).strip()})
    scale = (
        _float_or_none(record.get("scale_x", 1.0)),
        _float_or_none(record.get("scale_y", 1.0)),
        _float_or_none(record.get("scale_z", 1.0)),
    )
    rotation_value = _float_or_none(record.get("rotation", 0.0))
    transform_scale = (
        None if insert_scale is None
        else tuple(_float_or_none(item) for item in insert_scale)
    )
    transform_rotation = _float_or_none(insert_rotation)
    transform_facts = {
        "schema_version": "cad2gis-block-transform-facts-v1",
        "coordinate_system": "WCS",
        "insertion_point": _canonical_json_value(insertion_point_wcs),
        "insertion_point_status": str(insertion_point_status),
        "block_base_point": _canonical_json_value(block_base_point),
        "block_base_point_status": str(block_base_point_status),
        "scale": _canonical_json_value(transform_scale),
        "scale_status": str(insert_scale_status),
        "rotation": transform_rotation,
        "rotation_status": str(insert_rotation_status),
        "normal": _canonical_json_value(insert_normal),
        "normal_status": str(insert_normal_status),
        "extrusion": _canonical_json_value(insert_extrusion),
        "extrusion_status": str(insert_extrusion_status),
        "owner_handle": str(owner_handle or ""),
        "container_block_name": str(container_block_name or ""),
        "nesting_context": str(nesting_context or "unknown"),
    }
    raw = {
        "schema_version": RAW_PROPERTIES_SCHEMA,
        "extraction_backend": str(extraction_backend),
        "reader_backend_status": str(reader_backend_status),
        "bulk_protocol_schema": str(record.get("bulk_protocol_schema", "")),
        "object_name": str(record.get("object_name", "")),
        "dwg_type_name": str(record.get("dwg_type_name", "")),
        "handle": str(record.get("handle", "")),
        "owner_handle": str(owner_handle or ""),
        "layout": str(record.get("layout", "")),
        "layer": str(record.get("layer", "")),
        "block_name": str(record.get("block_name", "")),
        "block_effective_name": str(block_effective_name or ""),
        "block_reference_name": str(block_reference_name or ""),
        "raw_text": str(record.get("text", "") if raw_text is None else raw_text),
        "text": str(record.get("text", "")),
        "text_source": str(text_source or ""),
        "attribute_tags": sorted(attributes),
        "block_attributes": attributes,
        "dynamic_block_properties": _canonical_json_value(dynamic_block_properties or {}),
        "dynamic_block_properties_status": str(dynamic_block_properties_status or "unknown"),
        "dimension_measurement": _float_or_none(record.get("dimension_value")),
        "dimension_text_override": str(dimension_text_override or ""),
        "native_length": _float_or_none(record.get("native_length")),
        "native_length_source": str(native_length_source or ""),
        "curve_facts": _canonical_json_value(curve_facts or {}),
        "curve_fingerprint": str(curve_fingerprint or ""),
        # Instance transform facts are explicit and loss-aware.  In
        # particular, an unavailable block base point is JSON null; it is
        # never synthesized as the origin.
        "insertion_point": _canonical_json_value(insertion_point_wcs),
        "insertion_point_wcs": _canonical_json_value(insertion_point_wcs),
        "insertion_point_status": str(insertion_point_status),
        "block_base_point": _canonical_json_value(block_base_point),
        "block_base_point_status": str(block_base_point_status),
        "insert_normal": _canonical_json_value(insert_normal),
        "normal": _canonical_json_value(insert_normal),
        "insert_normal_status": str(insert_normal_status),
        "normal_status": str(insert_normal_status),
        "insert_extrusion": _canonical_json_value(insert_extrusion),
        "extrusion": _canonical_json_value(insert_extrusion),
        "insert_extrusion_status": str(insert_extrusion_status),
        "extrusion_status": str(insert_extrusion_status),
        "container_block_name": str(container_block_name or ""),
        "nesting_context": str(nesting_context or "unknown"),
        "block_definition_handle": str(block_definition_handle or ""),
        "block_flags": None if block_flags is None else int(block_flags),
        "external_reference_path": str(external_reference_path or ""),
        "external_reference_status": str(external_reference_status or "unknown"),
        "geometry_status": str(geometry_status or "unavailable"),
        "inventory_support_status": str(
            inventory_support_status or "inventory_only"
        ),
        "transform_facts": transform_facts,
        "transform_scale": _canonical_json_value(transform_scale),
        "transform_scale_status": str(insert_scale_status),
        "transform_rotation": transform_rotation,
        "transform_rotation_status": str(insert_rotation_status),
        "scale": _canonical_json_value(transform_scale),
        "scale_x": scale[0],
        "scale_y": scale[1],
        "scale_z": scale[2],
        "rotation": transform_rotation,
        "entity_rotation": rotation_value,
        "aci_color": int(record.get("aci_color", 256)),
        "true_color": str(record.get("true_color", "")),
        "linetype": str(record.get("linetype", "ByLayer")),
        "lineweight": int(record.get("lineweight", -1)),
        "entity_aci_color": int(record.get("entity_aci_color", record.get("aci_color", 256))),
        "layer_aci_color": int(record.get("layer_aci_color", 7)),
        "entity_true_color": str(record.get("entity_true_color", "")),
        "layer_true_color": str(record.get("layer_true_color", "")),
        "entity_linetype": str(record.get("entity_linetype", record.get("linetype", "ByLayer"))),
        "layer_linetype": str(record.get("layer_linetype", "Continuous")),
        "entity_lineweight": int(record.get("entity_lineweight", record.get("lineweight", -1))),
        "layer_lineweight": int(record.get("layer_lineweight", -1)),
        "unsupported_reason": ";".join(reasons),
        "unsupported_reasons": reasons,
    }
    return _canonical_json_value(raw)


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
            result.append({"t": "\t", "n": "\n", "r": "\r", "p": "|", "\\": "\\"}.get(code, code))
            index += 2
        else:
            result.append(value[index])
            index += 1
    return "".join(result)


def _parse_bulk_attributes(value, *, line_number=None):
    result = {}
    for index, pair in enumerate(value.split("|") if value else ()):
        if "=" not in pair:
            raise BulkProtocolError(
                f"attribute at index {index} has no '=' delimiter: {pair!r}",
                line_number=line_number,
                field_name="attributes",
            )
        key, item = pair.split("=", 1)
        key, item = _unescape_tsv(key).upper(), _unescape_tsv(item)
        if not key:
            raise BulkProtocolError(
                f"attribute at index {index} has an empty tag",
                line_number=line_number,
                field_name="attributes",
            )
        result[key] = item
    return result


def _parse_bulk_points(value, *, line_number=None, field_name="points"):
    points = []
    for index, item in enumerate(value.split(";") if value else ()):
        coordinates = item.split(",")
        if len(coordinates) != 2:
            raise BulkProtocolError(
                f"point at index {index} must contain exactly two coordinates: {item!r}",
                line_number=line_number,
                field_name=field_name,
            )
        point = tuple(_float_or_none(coordinate) for coordinate in coordinates)
        if any(coordinate is None for coordinate in point):
            raise BulkProtocolError(
                f"point at index {index} contains an invalid or non-finite coordinate: {item!r}",
                line_number=line_number,
                field_name=field_name,
            )
        points.append(point)
    return points


def _parse_bulk_points3(value, *, line_number=None, field_name="vertices_wcs"):
    points = []
    for index, item in enumerate(value.split(";") if value else ()):
        coordinates = item.split(",")
        if len(coordinates) != 3:
            raise BulkProtocolError(
                f"three-dimensional value at index {index} must contain exactly "
                f"three coordinates: {item!r}",
                line_number=line_number,
                field_name=field_name,
            )
        point = tuple(_float_or_none(coordinate) for coordinate in coordinates)
        if any(coordinate is None for coordinate in point):
            raise BulkProtocolError(
                f"three-dimensional value at index {index} contains an invalid or "
                f"non-finite coordinate: {item!r}",
                line_number=line_number,
                field_name=field_name,
            )
        points.append(point)
    return points


def _parse_bulk_numbers(value, *, field_name, line_number=None):
    result = []
    for index, item in enumerate(value.split(",") if value else ()):
        number = _float_or_none(item)
        if number is None:
            raise BulkProtocolError(
                f"value at index {index} is invalid or non-finite: {item!r}",
                line_number=line_number,
                field_name=field_name,
            )
        result.append(number)
    return result


def _parse_bulk_vector3(value, *, field_name, line_number=None):
    if not value:
        return None
    points = _parse_bulk_points3(
        value,
        line_number=line_number,
        field_name=field_name,
    )
    if len(points) != 1:
        raise BulkProtocolError(
            "must contain exactly one three-dimensional vector",
            line_number=line_number,
            field_name=field_name,
        )
    return points[0]


def _parse_json_object(value, *, field_name, line_number=None):
    def reject_constant(constant):
        raise ValueError(f"non-finite JSON number {constant!r}")

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            value,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BulkProtocolError(
            f"invalid JSON object: {exc}",
            line_number=line_number,
            field_name=field_name,
        ) from exc
    if not isinstance(parsed, dict):
        raise BulkProtocolError(
            f"JSON value must be an object, got {type(parsed).__name__}",
            line_number=line_number,
            field_name=field_name,
        )
    return parsed


def _parse_optional_bulk_float(value, *, field_name, line_number=None):
    if value is None or value == "":
        return None
    number = _float_or_none(value)
    if number is None:
        raise BulkProtocolError(
            f"value is invalid or non-finite: {value!r}",
            line_number=line_number,
            field_name=field_name,
        )
    return number


def _parse_required_bulk_float(value, *, field_name, line_number=None):
    if value is None or value == "":
        raise BulkProtocolError(
            "required finite numeric value is empty",
            line_number=line_number,
            field_name=field_name,
        )
    return _parse_optional_bulk_float(
        value, field_name=field_name, line_number=line_number,
    )


def _parse_required_bulk_int(value, *, field_name, line_number=None):
    if value is None or value == "":
        raise BulkProtocolError(
            "required integer value is empty",
            line_number=line_number,
            field_name=field_name,
        )
    try:
        # Reject JSON-like floats such as ``1.0`` rather than truncating them.
        return int(str(value), 10)
    except (TypeError, ValueError) as exc:
        raise BulkProtocolError(
            f"value must be a base-10 integer: {value!r}",
            line_number=line_number,
            field_name=field_name,
        ) from exc


def _parse_optional_bulk_int(value, *, field_name, line_number=None):
    if value is None or value == "":
        return None
    return _parse_required_bulk_int(
        value, field_name=field_name, line_number=line_number,
    )


def _parse_bulk_flag(value, *, field_name, line_number=None):
    result = _parse_required_bulk_int(
        value, field_name=field_name, line_number=line_number,
    )
    if result not in {0, 1}:
        raise BulkProtocolError(
            f"flag must be 0 or 1, got {result!r}",
            line_number=line_number,
            field_name=field_name,
        )
    return bool(result)


def _parse_bulk_status(
    value, *, field_name, allowed, default, line_number=None, required=False,
):
    status = _unescape_tsv(value).strip() if value is not None else ""
    if not status:
        if required:
            raise BulkProtocolError(
                "status is required by the current bulk protocol",
                line_number=line_number,
                field_name=field_name,
            )
        return default
    if status not in allowed:
        raise BulkProtocolError(
            f"unsupported status {status!r}; expected one of {sorted(allowed)}",
            line_number=line_number,
            field_name=field_name,
        )
    return status


def _parse_bulk_primitive_parameters(value, *, line_number=None):
    """Parse the compact AutoLISP payload while accepting canonical JSON too."""
    text = _unescape_tsv(value)
    if not text:
        return {}
    if text.lstrip().startswith("{"):
        return _parse_json_object(
            text,
            field_name="primitive_parameters",
            line_number=line_number,
        )
    result = {}
    point_fields = {"center_wcs", "major_axis"}
    point_list_fields = {"fit_points_wcs"}
    number_list_fields = {"knot_values", "weights"}
    integer_fields = {"degree", "flags"}
    for pair in text.split("|"):
        if not pair:
            raise BulkProtocolError(
                "compact parameter payload contains an empty pair",
                line_number=line_number,
                field_name="primitive_parameters",
            )
        if "=" not in pair:
            raise BulkProtocolError(
                f"compact parameter has no '=' delimiter: {pair!r}",
                line_number=line_number,
                field_name="primitive_parameters",
            )
        key, raw_value = pair.split("=", 1)
        if not key:
            raise BulkProtocolError(
                "compact parameter has an empty name",
                line_number=line_number,
                field_name="primitive_parameters",
            )
        if key in point_fields:
            result[key] = list(_parse_bulk_vector3(
                raw_value,
                field_name=f"primitive_parameters.{key}",
                line_number=line_number,
            ) or ())
        elif key in point_list_fields:
            result[key] = [list(point) for point in _parse_bulk_points3(
                raw_value,
                field_name=f"primitive_parameters.{key}",
                line_number=line_number,
            )]
        elif key in number_list_fields:
            result[key] = _parse_bulk_numbers(
                raw_value,
                field_name=f"primitive_parameters.{key}",
                line_number=line_number,
            )
        elif key in integer_fields:
            try:
                result[key] = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise BulkProtocolError(
                    f"{key} must be an integer: {raw_value!r}",
                    line_number=line_number,
                    field_name=f"primitive_parameters.{key}",
                ) from exc
        else:
            number = _float_or_none(raw_value)
            result[key] = number if number is not None else raw_value
    return result


def _curve_facts(
    *,
    primitive_type,
    vertices_wcs=(),
    bulges=None,
    elevation=None,
    normal=None,
    extrusion=None,
    closed=False,
    primitive_parameters=None,
    native_length=None,
    native_length_source="",
):
    primitive_type = str(primitive_type or "").upper()
    if primitive_type not in _CURVE_DWG_TYPES:
        return {}, ""
    facts = canonical_curve_facts({
        "schema_version": CURVE_FACTS_SCHEMA,
        "coordinate_system": "WCS",
        "primitive_type": primitive_type,
        "vertices_wcs": list(vertices_wcs),
        "bulges": bulges,
        "elevation": elevation,
        "normal": normal,
        "extrusion": extrusion,
        "closed": bool(closed),
        "primitive_parameters": primitive_parameters or {},
        "native_length": native_length,
        "native_length_source": native_length_source,
    })
    return facts, canonical_curve_fingerprint(facts)


def _record_from_bulk_row(columns, *, line_number=None):
    if len(columns) not in _BULK_ROW_LENGTHS:
        raise BulkProtocolError(
            f"unexpected column count {len(columns)}; expected one of "
            f"{sorted(_BULK_ROW_LENGTHS)}",
            line_number=line_number,
            field_name="column_count",
        )
    base = columns[:17]
    (kind, handle, layer, layout, aci, truecolor, linetype, lineweight,
     rotation, closed, block_name, text, attributes, point_text,
     radius_text, start_text, end_text) = base
    layer_aci, layer_truecolor, layer_linetype, layer_lineweight = (
        columns[17:21] if len(columns) >= 21 else ("7", "-1", "Continuous", "-1")
    )
    scale_x, scale_y, scale_z = columns[21:24] if len(columns) >= 24 else ("1", "1", "1")
    owner_handle, text_source, dimension_text_override, dynamic_text, unsupported_text = (
        columns[24:29] if len(columns) >= 29 else ("", "", "", "", "")
    )
    native_length_text = columns[29] if len(columns) >= 30 else ""
    (curve_schema, vertices_wcs_text, bulges_text, elevation_text,
     normal_text, extrusion_text, primitive_type_text, primitive_parameters_text) = (
        columns[30:38] if len(columns) >= 38 else ("", "", "", "", "", "", "", "")
    )
    (insertion_point_text, insertion_point_status_text,
     block_base_point_text, block_base_point_status_text,
     insert_normal_text, insert_normal_status_text,
     insert_extrusion_text, insert_extrusion_status_text,
     container_block_name_text, nesting_context_text,
     block_definition_handle_text, block_flags_text,
     external_reference_path_text, external_reference_status_text,
     geometry_status_text, inventory_support_status_text) = (
        columns[38:54]
        if len(columns) >= _INSTANCE_FACTS_ROW_LENGTH
        else ("", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "")
    )
    kind = _unescape_tsv(kind).upper()
    if not kind:
        raise BulkProtocolError(
            "entity type cannot be empty",
            line_number=line_number,
            field_name="kind",
        )
    points = _parse_bulk_points(point_text, line_number=line_number)
    if kind in {"CIRCLE", "ARC"} and points:
        center = points[0]
        radius = _parse_required_bulk_float(
            radius_text, field_name="radius", line_number=line_number,
        )
        if radius <= 0.0:
            raise BulkProtocolError(
                f"radius must be positive, got {radius!r}",
                line_number=line_number,
                field_name="radius",
            )
        start = (
            0.0 if kind == "CIRCLE" else _parse_required_bulk_float(
                start_text, field_name="start_angle", line_number=line_number,
            )
        )
        end = (
            2 * math.pi if kind == "CIRCLE" else _parse_required_bulk_float(
                end_text, field_name="end_angle", line_number=line_number,
            )
        )
        if end <= start:
            end += 2 * math.pi
        segments = 48 if kind == "CIRCLE" else 24
        points = [
            (center[0] + radius * math.cos(start + (end - start) * index / segments),
             center[1] + radius * math.sin(start + (end - start) * index / segments))
            for index in range(segments + 1)
        ]
    parsed_attributes = _parse_bulk_attributes(attributes, line_number=line_number)
    raw_text = _unescape_tsv(text)
    parsed_text = _plain_text(raw_text)
    if parsed_attributes:
        parsed_text = "\n".join(f"{key}={value}" for key, value in sorted(parsed_attributes.items()))
    centroid = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    ) if points else (0.0, 0.0)
    entity_aci = _parse_required_bulk_int(
        aci, field_name="entity_aci_color", line_number=line_number,
    )
    layer_aci_value = abs(_parse_required_bulk_int(
        layer_aci, field_name="layer_aci_color", line_number=line_number,
    ))
    entity_true_color = _parse_required_bulk_int(
        truecolor, field_name="entity_true_color", line_number=line_number,
    )
    layer_true_color = _parse_required_bulk_int(
        layer_truecolor, field_name="layer_true_color", line_number=line_number,
    )
    effective_aci = layer_aci_value if entity_aci == 256 else entity_aci
    effective_true_color = entity_true_color if entity_true_color >= 0 else layer_true_color
    effective_linetype = (
        _unescape_tsv(layer_linetype) or "Continuous"
        if _unescape_tsv(linetype).casefold() == "bylayer"
        else _unescape_tsv(linetype)
    )
    entity_lineweight_value = _parse_required_bulk_int(
        lineweight, field_name="entity_lineweight", line_number=line_number,
    )
    layer_lineweight_value = _parse_required_bulk_int(
        layer_lineweight, field_name="layer_lineweight", line_number=line_number,
    )
    effective_lineweight = (
        layer_lineweight_value
        if entity_lineweight_value < 0 else entity_lineweight_value
    )
    layout_name = _unescape_tsv(layout) or "Model"
    layout_role = classify_layout_role(layout_name)
    object_names = {
        "LINE": "ACDBLINE", "LWPOLYLINE": "ACDBLWPOLYLINE",
        "POLYLINE": "ACDBPOLYLINE", "CIRCLE": "ACDBCIRCLE",
        "ARC": "ACDBARC", "SPLINE": "ACDBSPLINE", "ELLIPSE": "ACDBELLIPSE",
        "POINT": "ACDBPOINT", "INSERT": "ACDBBLOCKREFERENCE",
        "TEXT": "ACDBTEXT", "MTEXT": "ACDBMTEXT",
        "ATTRIB": "ACDBATTRIBUTE", "ATTDEF": "ACDBATTRIBUTEDEFINITION",
        "MLEADER": "ACDBMLEADER", "MULTILEADER": "ACDBMLEADER",
        "TABLE": "ACDBTABLE",
        "DIMENSION": "ACDBDIMENSION",
        "BLOCK_RECORD": "ACDBBLOCKTABLERECORD",
        "XREF": "ACDBEXTERNALREFERENCE",
    }
    dimension_value = (
        _parse_optional_bulk_float(
            radius_text,
            field_name="dimension_measurement",
            line_number=line_number,
        )
        if kind == "DIMENSION" else None
    )
    dynamic_properties = _parse_json_object(
        _unescape_tsv(dynamic_text),
        field_name="dynamic_block_properties",
        line_number=line_number,
    ) if dynamic_text else {}
    inferred_text_source = {
        "TEXT": "entity_text", "MTEXT": "entity_text",
        "ATTRIB": "attribute_text", "ATTDEF": "attribute_text",
        "MLEADER": "multileader_text", "MULTILEADER": "multileader_text",
        "TABLE": "table_cells", "DIMENSION": "dimension_text_override",
    }.get(kind, "block_attributes" if kind == "INSERT" and parsed_attributes else "")
    unsupported_reasons = [
        item for item in _unescape_tsv(unsupported_text).split(";") if item
    ]
    if len(columns) < 29:
        unsupported_reasons.append("legacy_bulk_protocol_without_raw_extension")
    if kind in _CURVE_DWG_TYPES and len(columns) < 38:
        unsupported_reasons.append("legacy_bulk_protocol_without_curve_facts")
    if kind == "INSERT" and not dynamic_properties and not any(
        "dynamic_block_properties" in item for item in unsupported_reasons
    ):
        unsupported_reasons.append("dynamic_block_properties_unavailable_in_bulk_backend")
    if kind in {"MLEADER", "MULTILEADER"} and not parsed_text:
        unsupported_reasons.append("multileader_text_unavailable_in_bulk_backend")
    if kind == "TABLE" and not parsed_text:
        unsupported_reasons.append("table_text_unavailable_in_bulk_backend")
    closed_value = _parse_bulk_flag(
        closed, field_name="closed", line_number=line_number,
    )
    rotation_value = _parse_required_bulk_float(
        rotation, field_name="rotation", line_number=line_number,
    )
    scale_values = (
        _parse_required_bulk_float(
            scale_x, field_name="scale_x", line_number=line_number,
        ),
        _parse_required_bulk_float(
            scale_y, field_name="scale_y", line_number=line_number,
        ),
        _parse_required_bulk_float(
            scale_z, field_name="scale_z", line_number=line_number,
        ),
    )
    if kind == "INSERT" and any(value == 0.0 for value in scale_values):
        unsupported_reasons.append("zero_insert_scale")

    is_extended_instance_protocol = len(columns) >= _INSTANCE_FACTS_ROW_LENGTH
    is_insert = kind == "INSERT"
    insertion_point_wcs = (
        _parse_bulk_vector3(
            insertion_point_text,
            field_name="insertion_point_wcs",
            line_number=line_number,
        )
        if insertion_point_text else None
    )
    block_base_point = (
        _parse_bulk_vector3(
            block_base_point_text,
            field_name="block_base_point",
            line_number=line_number,
        )
        if block_base_point_text else None
    )
    insert_normal = (
        _parse_bulk_vector3(
            insert_normal_text,
            field_name="insert_normal",
            line_number=line_number,
        )
        if insert_normal_text else None
    )
    insert_extrusion = (
        _parse_bulk_vector3(
            insert_extrusion_text,
            field_name="insert_extrusion",
            line_number=line_number,
        )
        if insert_extrusion_text else None
    )
    legacy_instance_status = "legacy_unavailable" if is_insert else "not_applicable"
    insertion_point_status = _parse_bulk_status(
        insertion_point_status_text,
        field_name="insertion_point_status",
        allowed=_INSTANCE_STATUS_VALUES,
        default=(
            "available" if insertion_point_wcs is not None
            else legacy_instance_status
        ),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    block_base_point_status = _parse_bulk_status(
        block_base_point_status_text,
        field_name="block_base_point_status",
        allowed=_INSTANCE_STATUS_VALUES,
        default=(
            "available" if block_base_point is not None
            else legacy_instance_status
        ),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    insert_normal_status = _parse_bulk_status(
        insert_normal_status_text,
        field_name="insert_normal_status",
        allowed=_INSTANCE_STATUS_VALUES,
        default=("available" if insert_normal is not None else legacy_instance_status),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    insert_extrusion_status = _parse_bulk_status(
        insert_extrusion_status_text,
        field_name="insert_extrusion_status",
        allowed=_INSTANCE_STATUS_VALUES,
        default=(
            "available" if insert_extrusion is not None
            else legacy_instance_status
        ),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    for field_name, value, status in (
        ("insertion_point_wcs", insertion_point_wcs, insertion_point_status),
        ("block_base_point", block_base_point, block_base_point_status),
        ("insert_normal", insert_normal, insert_normal_status),
        ("insert_extrusion", insert_extrusion, insert_extrusion_status),
    ):
        if (value is None) == (status == "available"):
            raise BulkProtocolError(
                "status 'available' requires a value and every other status "
                "requires an empty value",
                line_number=line_number,
                field_name=field_name,
            )
    if not is_insert and kind != "BLOCK_RECORD" and any(
        value is not None
        for value in (
            insertion_point_wcs, block_base_point, insert_normal, insert_extrusion,
        )
    ):
        raise BulkProtocolError(
            "instance transform facts are only valid for INSERT/BLOCK_RECORD rows",
            line_number=line_number,
            field_name="instance_facts",
        )

    container_block_name = _unescape_tsv(container_block_name_text)
    if not container_block_name and layout_name.upper().startswith("BLOCKDEF:"):
        container_block_name = layout_name.split(":", 1)[1]
    nesting_context = _parse_bulk_status(
        nesting_context_text,
        field_name="nesting_context",
        allowed=_NESTING_CONTEXT_VALUES,
        default=(
            "block_definition"
            if layout_name.upper().startswith("BLOCKDEF:")
            else "drawing_space"
        ),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    external_reference_path = _unescape_tsv(external_reference_path_text)
    external_reference_status = _parse_bulk_status(
        external_reference_status_text,
        field_name="external_reference_status",
        allowed=_EXTERNAL_REFERENCE_STATUS_VALUES,
        default=("legacy_unknown" if is_insert and not is_extended_instance_protocol else "not_external"),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    geometry_status = _parse_bulk_status(
        geometry_status_text,
        field_name="geometry_status",
        allowed=_GEOMETRY_STATUS_VALUES,
        default=("available" if points else "unavailable"),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    inventory_support_status = _parse_bulk_status(
        inventory_support_status_text,
        field_name="inventory_support_status",
        allowed=_INVENTORY_SUPPORT_VALUES,
        default=("legacy" if not is_extended_instance_protocol else "unknown"),
        line_number=line_number,
        required=is_extended_instance_protocol,
    )
    if inventory_support_status == "inventory_only" and not any(
        reason.startswith("geometry_") or reason.startswith("external_reference_")
        for reason in unsupported_reasons
    ):
        unsupported_reasons.append("geometry_unsupported_in_bulk_backend")
    record = {
        "handle": _unescape_tsv(handle), "object_name": object_names.get(kind, f"ACDB{kind}"),
        "dwg_type_name": kind, "layout": layout_name, "layout_role": layout_role,
        "cad_role": layout_role, "layer": _unescape_tsv(layer) or "0",
        "points": points, "centroid": centroid, "closed": closed_value,
        "text": parsed_text, "block_name": _unescape_tsv(block_name),
        "block_attributes": parsed_attributes, "aci_color": effective_aci,
        "true_color": f"#{effective_true_color & 0xFFFFFF:06X}" if effective_true_color >= 0 else "",
        "linetype": effective_linetype or "Continuous", "lineweight": effective_lineweight,
        "rotation": rotation_value,
        "entity_aci_color": entity_aci, "layer_aci_color": layer_aci_value,
        "entity_true_color": f"#{entity_true_color & 0xFFFFFF:06X}" if entity_true_color >= 0 else "",
        "layer_true_color": f"#{layer_true_color & 0xFFFFFF:06X}" if layer_true_color >= 0 else "",
        "entity_linetype": _unescape_tsv(linetype) or "ByLayer",
        "layer_linetype": _unescape_tsv(layer_linetype) or "Continuous",
        "entity_lineweight": entity_lineweight_value,
        "layer_lineweight": layer_lineweight_value,
        "scale_x": scale_values[0], "scale_y": scale_values[1],
        "scale_z": scale_values[2],
        "dimension_value": dimension_value,
        "dimension_text_override": _unescape_tsv(dimension_text_override),
        "owner_handle": _unescape_tsv(owner_handle),
        "insertion_point": insertion_point_wcs,
        "insertion_point_wcs": insertion_point_wcs,
        "insertion_point_status": insertion_point_status,
        "block_base_point": block_base_point,
        "block_base_point_status": block_base_point_status,
        "insert_normal": insert_normal,
        "normal": insert_normal,
        "insert_normal_status": insert_normal_status,
        "insert_extrusion": insert_extrusion,
        "extrusion": insert_extrusion,
        "insert_extrusion_status": insert_extrusion_status,
        "container_block_name": container_block_name,
        "nesting_context": nesting_context,
        "block_definition_handle": _unescape_tsv(block_definition_handle_text),
        "block_flags": _parse_optional_bulk_int(
            block_flags_text,
            field_name="block_flags",
            line_number=line_number,
        ),
        "external_reference_path": external_reference_path,
        "external_reference_status": external_reference_status,
        "geometry_status": geometry_status,
        "inventory_support_status": inventory_support_status,
        "scale": scale_values if is_insert else None,
        "bulk_protocol_schema": (
            BULK_PROTOCOL_SCHEMA
            if is_extended_instance_protocol else f"legacy-columns-{len(columns)}"
        ),
        "native_length": _parse_optional_bulk_float(
            native_length_text,
            field_name="native_length",
            line_number=line_number,
        ),
    }
    if curve_schema:
        schema = _unescape_tsv(curve_schema)
        if schema != CURVE_FACTS_SCHEMA:
            raise BulkProtocolError(
                f"Unsupported bulk curve facts schema: {schema!r}; "
                f"expected {CURVE_FACTS_SCHEMA!r}",
                line_number=line_number,
                field_name="curve_schema",
            )
        vertices_wcs = _parse_bulk_points3(
            vertices_wcs_text,
            line_number=line_number,
            field_name="vertices_wcs",
        )
        curve_bulges = _parse_bulk_numbers(
            bulges_text,
            field_name="bulges",
            line_number=line_number,
        )
        if len(curve_bulges) != len(vertices_wcs):
            raise BulkProtocolError(
                "Bulk curve facts require one bulge per WCS vertex: "
                f"kind={kind}, handle={_unescape_tsv(handle)!r}, "
                f"vertices={len(vertices_wcs)}, bulges={len(curve_bulges)}",
                line_number=line_number,
                field_name="bulges",
            )
        curve_facts, curve_fingerprint = _curve_facts(
            primitive_type=_unescape_tsv(primitive_type_text) or kind,
            vertices_wcs=vertices_wcs,
            bulges=curve_bulges,
            elevation=_parse_optional_bulk_float(
                elevation_text,
                field_name="elevation",
                line_number=line_number,
            ),
            normal=_parse_bulk_vector3(
                normal_text,
                field_name="normal",
                line_number=line_number,
            ),
            extrusion=_parse_bulk_vector3(
                extrusion_text,
                field_name="extrusion",
                line_number=line_number,
            ),
            closed=closed_value,
            primitive_parameters=_parse_bulk_primitive_parameters(
                primitive_parameters_text,
                line_number=line_number,
            ),
            native_length=record["native_length"],
            native_length_source=(
                "autocad_curve_distance" if record["native_length"] is not None else ""
            ),
        )
    else:
        curve_facts, curve_fingerprint = {}, ""
    record["curve_facts"] = curve_facts
    record["curve_fingerprint"] = curve_fingerprint
    record["raw_properties"] = _canonical_raw_properties(
        record,
        extraction_backend="autocad_core_console_bulk",
        reader_backend_status="authoritative",
        owner_handle=record["owner_handle"],
        raw_text=raw_text,
        text_source=_unescape_tsv(text_source) or inferred_text_source,
        dimension_text_override=record["dimension_text_override"],
        dynamic_block_properties=dynamic_properties,
        dynamic_block_properties_status=(
            "available" if dynamic_properties else
            "unsupported_by_core_console_bulk" if kind == "INSERT" else
            "not_applicable"
        ),
        block_reference_name=record["block_name"] if kind == "INSERT" else "",
        native_length_source=(
            "autocad_curve_distance" if record["native_length"] is not None else ""
        ),
        curve_facts=curve_facts,
        curve_fingerprint=curve_fingerprint,
        insertion_point_wcs=insertion_point_wcs,
        insertion_point_status=insertion_point_status,
        block_base_point=block_base_point,
        block_base_point_status=block_base_point_status,
        insert_normal=insert_normal,
        insert_normal_status=insert_normal_status,
        insert_extrusion=insert_extrusion,
        insert_extrusion_status=insert_extrusion_status,
        insert_scale=(scale_values if is_insert else None),
        insert_scale_status=("available" if is_insert else "not_applicable"),
        insert_rotation=(rotation_value if is_insert else None),
        insert_rotation_status=("available" if is_insert else "not_applicable"),
        container_block_name=container_block_name,
        nesting_context=nesting_context,
        block_definition_handle=record["block_definition_handle"],
        block_flags=record["block_flags"],
        external_reference_path=external_reference_path,
        external_reference_status=external_reference_status,
        geometry_status=geometry_status,
        inventory_support_status=inventory_support_status,
        unsupported_reasons=unsupported_reasons,
    )
    return record


def _process_output_detail(completed):
    chunks = []
    for value in (getattr(completed, "stdout", b""), getattr(completed, "stderr", b"")):
        if isinstance(value, bytes):
            chunks.append(value.decode("utf-16-le", errors="replace"))
        else:
            chunks.append(str(value or ""))
    return "".join(chunks)[-4000:]


def _extract_records_with_core_console(
    dwg_path,
    accoreconsole=None,
    timeout=None,
    compatibility_policy=BULK_POLICY_STRICT,
):
    accoreconsole, accoreconsole_source = _configured_accoreconsole_path(accoreconsole)
    timeout_seconds, timeout_source = _resolve_accoreconsole_timeout(timeout)
    compatibility_policy = _validate_bulk_compatibility_policy(compatibility_policy)
    diagnostics = {
        "protocol_schema": BULK_PROTOCOL_SCHEMA,
        "compatibility_policy": compatibility_policy,
        "accoreconsole_path": str(accoreconsole),
        "accoreconsole_source": accoreconsole_source,
        "timeout_seconds": timeout_seconds,
        "timeout_source": timeout_source,
        "total_rows": 0,
        "parsed_rows": 0,
        "skipped_rows": 0,
        "skipped_row_errors": [],
    }
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
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "AutoCAD direct database extraction timed out after "
                f"{timeout_seconds:g} seconds"
            ) from exc
        if completed.returncode != 0 or not output_path.is_file():
            detail = _process_output_detail(completed)
            raise RuntimeError(f"AutoCAD direct database extraction failed ({completed.returncode}): {detail}")
        grouped = {}
        with output_path.open("r", encoding="utf-8-sig", errors="strict", newline="") as stream:
            for line_number, line in enumerate(stream, start=1):
                diagnostics["total_rows"] += 1
                try:
                    record = _record_from_bulk_row(
                        line.rstrip("\r\n").split("\t"),
                        line_number=line_number,
                    )
                except BulkProtocolError as exc:
                    if compatibility_policy != BULK_POLICY_SKIP_MALFORMED:
                        raise BulkProtocolViolation(
                            f"AutoCAD bulk protocol violation: {exc}"
                        ) from exc
                    diagnostics["skipped_rows"] += 1
                    diagnostics["skipped_row_errors"].append({
                        "line_number": exc.line_number,
                        "field": exc.field_name,
                        "error": str(exc),
                    })
                    continue
                except Exception as exc:
                    violation = BulkProtocolError(
                        str(exc),
                        line_number=line_number,
                        field_name="row",
                    )
                    if compatibility_policy != BULK_POLICY_SKIP_MALFORMED:
                        raise BulkProtocolViolation(
                            f"AutoCAD bulk protocol violation: {violation}"
                        ) from exc
                    diagnostics["skipped_rows"] += 1
                    diagnostics["skipped_row_errors"].append({
                        "line_number": line_number,
                        "field": "row",
                        "error": str(violation),
                    })
                    continue
                diagnostics["parsed_rows"] += 1
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
        entity_count = sum(
            record.get("dwg_type_name") != "DOCUMENT_METADATA"
            for _, _, records in result
            for record in records
        )
        if entity_count == 0:
            detail = _process_output_detail(completed)
            raise RuntimeError(
                "AutoCAD Core Console returned no CAD entity rows; "
                f"inventory is incomplete (skipped_rows={diagnostics['skipped_rows']}): {detail}"
            )
        diagnostics["entity_rows"] = entity_count
        return BulkExtractionResult(result, diagnostics=diagnostics)


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


def _com_property_status(obj, name):
    """Read one COM property without collapsing unreadable into a default.

    The regular ``_safe_get`` helper intentionally keeps non-curve records
    flowing when AutoCAD omits an optional property.  Curve delivery facts are
    different: a missing property and a property that raises are evidence that
    the fact is unavailable, not permission to invent a coordinate/default.
    """
    try:
        value = _retry_com(lambda: getattr(obj, name))
    except AttributeError:
        return None, "missing"
    except Exception:
        return None, "unreadable"
    if value is None:
        return None, "null"
    return value, "available"


def _com_vector3_property(entity, name):
    """Return a finite three-component COM vector and its read status."""
    value, status = _com_property_status(entity, name)
    if status != "available":
        return None, status
    try:
        coordinates = list(value)
    except Exception:
        return None, "unreadable"
    # Do not let _xyz's historical two-component/+Z convenience leak into
    # authoritative CABLE curve facts.
    if len(coordinates) != 3:
        return None, "invalid"
    vector = tuple(_float_or_none(item) for item in coordinates)
    if any(item is None for item in vector):
        return None, "invalid"
    return vector, "available"


def _append_curve_read_reason(reasons, field, status):
    """Persist an explicit unavailable/unsupported curve fact reason."""
    if status in {"missing", "unsupported"}:
        reason = f"curve_{field}_unsupported_in_com_backend"
    elif status == "invalid":
        reason = f"curve_{field}_invalid_in_com_backend"
    elif status == "null":
        reason = f"curve_{field}_unreadable_in_com_backend"
    else:
        reason = f"curve_{field}_{status}_in_com_backend"
    if reason not in reasons:
        reasons.append(reason)


def _strict_com_coordinates(entity, property_name, stride, reasons, *, min_points=2):
    """Read a COM flat coordinate array with exact stride/cardinality."""
    values, status = _com_property_status(entity, property_name)
    if status != "available":
        _append_curve_read_reason(reasons, "coordinates", status)
        return []
    try:
        values = list(values)
    except Exception:
        _append_curve_read_reason(reasons, "coordinates", "unreadable")
        return []
    if len(values) < stride * min_points or len(values) % stride:
        _append_curve_read_reason(
            reasons, "coordinates_stride_or_cardinality", "invalid"
        )
        return []
    numbers = [_float_or_none(item) for item in values]
    if any(number is None for number in numbers):
        _append_curve_read_reason(
            reasons, "coordinates_stride_or_cardinality", "invalid"
        )
        return []
    return [
        tuple(numbers[index + offset] for offset in range(stride))
        for index in range(0, len(numbers), stride)
    ]


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
    specifications = [(
        "TEXT,MTEXT,ATTRIB,ATTDEF,INSERT,MULTILEADER,MLEADER,TABLE,DIMENSION",
        None,
    )]
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


def _xyz(value):
    try:
        coordinates = list(value)
        if len(coordinates) < 2:
            return None
        result = (
            _float_or_none(coordinates[0]),
            _float_or_none(coordinates[1]),
            _float_or_none(coordinates[2] if len(coordinates) > 2 else 0.0),
        )
    except Exception:
        return None
    return None if any(coordinate is None for coordinate in result) else result


def _flat_points(values, stride):
    try:
        numbers = [float(value) for value in values]
    except Exception:
        return []
    return [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers) - 1, stride)]


def _flat_points3(values, stride=3):
    try:
        numbers = [float(value) for value in values]
    except Exception:
        return []
    points = []
    for index in range(0, len(numbers) - (stride - 1), stride):
        z = numbers[index + 2] if stride >= 3 else 0.0
        point = (numbers[index], numbers[index + 1], z)
        if all(math.isfinite(coordinate) for coordinate in point):
            points.append(point)
    return points


def _unit_vector(value):
    vector = _xyz(value)
    if vector is None:
        return None
    magnitude = math.sqrt(sum(coordinate * coordinate for coordinate in vector))
    if magnitude <= 0:
        return None
    return tuple(coordinate / magnitude for coordinate in vector)


def _cross(left, right):
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _ocs_to_wcs(point, extrusion):
    """Apply AutoCAD's arbitrary-axis algorithm to one OCS point."""
    normal = _unit_vector(extrusion) or (0.0, 0.0, 1.0)
    reference = (
        (0.0, 1.0, 0.0)
        if abs(normal[0]) < (1.0 / 64.0) and abs(normal[1]) < (1.0 / 64.0)
        else (0.0, 0.0, 1.0)
    )
    axis_x = _unit_vector(_cross(reference, normal))
    if axis_x is None:
        axis_x = (1.0, 0.0, 0.0)
    axis_y = _cross(normal, axis_x)
    return tuple(
        point[0] * axis_x[index]
        + point[1] * axis_y[index]
        + point[2] * normal[index]
        for index in range(3)
    )


def _com_curve_vertices(
    entity, object_name, elevation, extrusion, *, strict=False, reasons=None,
):
    reasons = reasons if reasons is not None else []
    if object_name == "ACDBLINE":
        return [
            point for point in (
                _xyz(_safe_get(entity, "StartPoint")),
                _xyz(_safe_get(entity, "EndPoint")),
            ) if point is not None
        ]
    if object_name in {"ACDBPOLYLINE", "ACDBLWPOLYLINE"}:
        # AutoCAD ActiveX exposes AcadLWPolyline as AcDbPolyline (not
        # AcDbLWPolyline) and its Coordinates array is OCS X/Y pairs.
        coordinate_values = (
            _strict_com_coordinates(entity, "Coordinates", 2, reasons)
            if strict else
            _flat_points3(_safe_get(entity, "Coordinates", ()), stride=2)
        )
        return [
            _ocs_to_wcs((coordinates[0], coordinates[1], elevation or 0.0), extrusion)
            for coordinates in coordinate_values
        ]
    if object_name == "ACDB2DPOLYLINE":
        vertices = (
            _strict_com_coordinates(entity, "Coordinates", 3, reasons)
            if strict else
            _flat_points3(_safe_get(entity, "Coordinates", ()), stride=3)
        )
        return [
            _ocs_to_wcs(
                # ActiveX documents the Z slot of AcadPolyline.Coordinates as
                # ignored; the entity Elevation is the authoritative OCS Z.
                (point[0], point[1], elevation or 0.0),
                extrusion,
            )
            for point in vertices
        ]
    if object_name == "ACDB3DPOLYLINE":
        return _flat_points3(_safe_get(entity, "Coordinates", ()), stride=3)
    if object_name == "ACDBSPLINE":
        return _flat_points3(
            _safe_get(entity, "ControlPoints", _safe_get(entity, "Coordinates", ())),
            stride=3,
        )
    return []


def _com_curve_bulges(
    entity, object_name, vertex_count, *, strict=False, reasons=None,
    allow_missing=False,
):
    reasons = reasons if reasons is not None else []
    if vertex_count == 0:
        return []
    if object_name not in {"ACDBLWPOLYLINE", "ACDBPOLYLINE", "ACDB2DPOLYLINE"}:
        return [0.0] * vertex_count
    if strict:
        method, status = _com_property_status(entity, "GetBulge")
        if status != "available" or not callable(method):
            if allow_missing and status in {"missing", "unsupported"}:
                reasons.append("curve_bulges_unsupported_in_com_backend")
                return [0.0] * vertex_count
            _append_curve_read_reason(reasons, "bulges", status if status != "available" else "invalid")
            return []
    else:
        method = _safe_get(entity, "GetBulge")
    result = []
    for index in range(vertex_count):
        value = None
        if callable(method):
            try:
                value = _float_or_none(_retry_com(lambda index=index: method(index)))
            except Exception:
                if strict:
                    _append_curve_read_reason(reasons, "bulges", "unreadable")
                    return []
                value = None
        if value is None:
            if strict:
                _append_curve_read_reason(reasons, "bulges", "unreadable")
                return []
            result.append(0.0)
        else:
            result.append(value)
    return result


def _finite_com_values(value):
    try:
        values = list(value)
    except Exception:
        return []
    result = []
    for item in values:
        number = _float_or_none(item)
        if number is not None:
            result.append(number)
    return result


def _com_primitive_parameters(entity, object_name):
    result = {}
    if object_name == "ACDB2DPOLYLINE":
        polyline_type = _float_or_none(_safe_get(entity, "Type"))
        if polyline_type is not None:
            result["polyline_type"] = int(polyline_type)
    if object_name in {"ACDBCIRCLE", "ACDBARC", "ACDBELLIPSE"}:
        center = _xyz(_safe_get(entity, "Center"))
        if center is not None:
            result["center_wcs"] = list(center)
    if object_name in {"ACDBCIRCLE", "ACDBARC"}:
        radius = _float_or_none(_safe_get(entity, "Radius"))
        if radius is not None:
            result["radius"] = radius
    if object_name == "ACDBARC":
        for key, property_name in (
            ("start_angle", "StartAngle"), ("end_angle", "EndAngle"),
        ):
            value = _float_or_none(_safe_get(entity, property_name))
            if value is not None:
                result[key] = value
    elif object_name == "ACDBELLIPSE":
        major_axis = _xyz(_safe_get(entity, "MajorAxis"))
        if major_axis is not None:
            result["major_axis"] = list(major_axis)
        for key, names in (
            ("radius_ratio", ("RadiusRatio",)),
            ("start_parameter", ("StartParameter", "StartAngle")),
            ("end_parameter", ("EndParameter", "EndAngle")),
        ):
            value = None
            for property_name in names:
                value = _float_or_none(_safe_get(entity, property_name))
                if value is not None:
                    break
            if value is not None:
                result[key] = value
    elif object_name == "ACDBSPLINE":
        degree = _float_or_none(_safe_get(entity, "Degree"))
        if degree is not None:
            result["degree"] = int(degree)
        knots = _finite_com_values(_safe_get(entity, "Knots", ()))
        weights = _finite_com_values(_safe_get(entity, "Weights", ()))
        fit_points = _flat_points3(_safe_get(entity, "FitPoints", ()), stride=3)
        if knots:
            result["knot_values"] = knots
        if weights:
            result["weights"] = weights
        if fit_points:
            result["fit_points_wcs"] = [list(point) for point in fit_points]
    return result


def _com_curve_facts(
    entity, object_name, closed, native_length, native_length_source,
    *, strict=False, unsupported_reasons=None,
):
    unsupported_reasons = unsupported_reasons if unsupported_reasons is not None else []
    primitive_type = {
        "ACDBPOLYLINE": "LWPOLYLINE",
        "ACDBLWPOLYLINE": "LWPOLYLINE",
        "ACDB2DPOLYLINE": "2DPOLYLINE",
        "ACDB3DPOLYLINE": "3DPOLYLINE",
    }.get(object_name, object_name.removeprefix("ACDB").upper())
    if primitive_type not in _CURVE_DWG_TYPES:
        return {}, ""
    initial_reason_count = len(unsupported_reasons)
    if strict:
        raw_normal, normal_status = _com_vector3_property(entity, "Normal")
        raw_extrusion, extrusion_status = _com_vector3_property(
            entity, "ExtrusionDirection"
        )
        if raw_normal is None and raw_extrusion is None:
            _append_curve_read_reason(unsupported_reasons, "normal", normal_status)
            _append_curve_read_reason(unsupported_reasons, "extrusion", extrusion_status)
            return {}, ""
        if raw_normal is None:
            if normal_status not in {"missing", "unsupported"}:
                _append_curve_read_reason(unsupported_reasons, "normal", normal_status)
            normal = raw_extrusion
        else:
            normal = raw_normal
        if raw_extrusion is None:
            if extrusion_status not in {"missing", "unsupported"}:
                _append_curve_read_reason(unsupported_reasons, "extrusion", extrusion_status)
            extrusion = raw_normal
        else:
            extrusion = raw_extrusion
        if primitive_type in {"LWPOLYLINE", "POLYLINE", "2DPOLYLINE"}:
            raw_elevation, elevation_status = _com_property_status(entity, "Elevation")
            elevation = (
                _float_or_none(raw_elevation)
                if elevation_status == "available" else None
            )
            if elevation is None:
                _append_curve_read_reason(unsupported_reasons, "elevation", elevation_status)
        else:
            elevation = None
    else:
        raw_normal = _xyz(_safe_get(entity, "Normal"))
        raw_extrusion = _xyz(_safe_get(entity, "ExtrusionDirection"))
        normal = raw_normal or raw_extrusion or (0.0, 0.0, 1.0)
        extrusion = raw_extrusion or raw_normal or (0.0, 0.0, 1.0)
        elevation = (
            _float_or_none(_safe_get(entity, "Elevation", 0.0))
            if primitive_type in {"LWPOLYLINE", "POLYLINE", "2DPOLYLINE"}
            else None
        )
    vertices = _com_curve_vertices(
        entity, object_name, elevation, extrusion,
        strict=strict, reasons=unsupported_reasons,
    )
    primitive_parameters = _com_primitive_parameters(entity, object_name)
    allow_missing_bulge = (
        strict
        and object_name == "ACDB2DPOLYLINE"
        and primitive_parameters.get("polyline_type") not in {None, 0}
    )
    bulges = _com_curve_bulges(
        entity, object_name, len(vertices),
        strict=strict, reasons=unsupported_reasons,
        allow_missing=allow_missing_bulge,
    )
    new_reasons = unsupported_reasons[initial_reason_count:]
    if strict and any(
        reason != "curve_bulges_unsupported_in_com_backend"
        for reason in new_reasons
    ):
        return {}, ""
    if strict and new_reasons and not allow_missing_bulge:
        return {}, ""
    return _curve_facts(
        primitive_type=primitive_type,
        vertices_wcs=vertices,
        bulges=bulges,
        elevation=elevation,
        normal=normal,
        extrusion=extrusion,
        closed=closed,
        primitive_parameters=primitive_parameters,
        native_length=native_length,
        native_length_source=native_length_source,
    )


def _plain_text(value):
    text = str(value or "").replace("\\P", "\n")
    text = re.sub(r"\\[A-Za-z][^;]*;", "", text)
    return text.replace("{", "").replace("}", "").strip()


def _table_text(entity):
    rows = int(_safe_get(entity, "Rows", 0) or 0)
    columns = int(_safe_get(entity, "Columns", 0) or 0)
    if rows <= 0 or columns <= 0:
        return ""
    output = []
    for row_index in range(rows):
        row = []
        for column_index in range(columns):
            try:
                value = _retry_com(
                    lambda row_index=row_index, column_index=column_index:
                    entity.GetText(row_index, column_index)
                )
            except Exception:
                value = ""
            row.append(_plain_text(value))
        if any(row):
            output.append("\t".join(row).rstrip())
    return "\n".join(output).strip()


def _entity_text_facts(entity, object_name):
    """Return normalized text, raw text and its deterministic CAD source."""
    raw = ""
    source = ""
    if object_name == "ACDBTABLE":
        raw = _table_text(entity)
        source = "table_cells"
    elif object_name in {"ACDBMLEADER", "ACDBMULTILEADER"}:
        raw = str(_safe_get(entity, "TextString", _safe_get(entity, "Contents", "")) or "")
        if not raw:
            mtext = _safe_get(entity, "MText")
            if mtext is not None:
                raw = str(_safe_get(mtext, "TextString", _safe_get(mtext, "Contents", "")) or "")
        source = "multileader_text"
    elif object_name in _TEXT_OBJECTS:
        raw = str(_safe_get(entity, "TextString", _safe_get(entity, "Contents", "")) or "")
        source = (
            "attribute_text"
            if object_name in {
                "ACDBATTRIBUTE", "ACDBATTRIBUTEREFERENCE", "ACDBATTRIBUTEDEFINITION",
            }
            else "entity_text"
        )
    elif "DIMENSION" in object_name:
        raw = str(_safe_get(entity, "TextOverride", "") or "")
        source = "dimension_text_override"
    return _plain_text(raw), raw, source


def _entity_text(entity, object_name):
    return _entity_text_facts(entity, object_name)[0]


def _block_attribute_facts(entity):
    result = {}
    unsupported = []
    # Constant attributes live on the block reference separately from editable
    # attribute references, so both collections are part of the instance facts.
    for method_name in ("GetConstantAttributes", "GetAttributes"):
        method = _safe_get(entity, method_name)
        if not callable(method):
            continue
        try:
            attributes = _retry_com(method)
        except Exception:
            unsupported.append(f"{method_name}_unavailable")
            continue
        for attribute in attributes or ():
            tag = str(_safe_get(attribute, "TagString", "")).strip().upper()
            value = _plain_text(_safe_get(attribute, "TextString", ""))
            if tag:
                result[tag] = value
    return result, unsupported


def _block_attributes(entity):
    return _block_attribute_facts(entity)[0]


def _dynamic_block_facts(entity):
    is_dynamic = bool(_safe_get(entity, "IsDynamicBlock", False))
    if not is_dynamic:
        return {}, "not_dynamic", []
    method = _safe_get(entity, "GetDynamicBlockProperties")
    if not callable(method):
        return {}, "unsupported", ["dynamic_block_properties_method_unavailable"]
    try:
        properties = _retry_com(method)
    except Exception:
        return {}, "unreadable", ["dynamic_block_properties_read_failed"]
    result = {}
    for index, prop in enumerate(properties or ()):
        name = str(_safe_get(prop, "PropertyName", "")).strip() or f"PROPERTY_{index}"
        result[name] = {
            "value": _canonical_json_value(_safe_get(prop, "Value")),
            "read_only": bool(_safe_get(prop, "ReadOnly", False)),
            "allowed_values": _canonical_json_value(_safe_get(prop, "AllowedValues", ())),
        }
    return result, "available", []


def _com_finite_number_property(entity, name):
    value, status = _com_property_status(entity, name)
    if status != "available":
        return None, "unavailable", status
    number = _float_or_none(value)
    if number is None:
        return None, "unavailable", "invalid"
    return number, "available", "available"


def _com_vector_alias(entity, names):
    """Read the first explicit finite vector; never manufacture an origin."""
    observed = []
    for name in names:
        value, status = _com_vector3_property(entity, name)
        observed.append((name, status))
        if status == "available":
            return value, "available", name, observed
    return None, "unavailable", "", observed


def _com_text_alias(entity, names):
    observed = []
    for name in names:
        value, status = _com_property_status(entity, name)
        observed.append((name, status))
        if status == "available" and str(value).strip():
            return str(value), "available", name, observed
    return "", "unavailable", "", observed


def _com_instance_facts(entity, object_name, layout_name):
    """Return explicit block-instance facts and loss reasons for COM fallback."""
    is_insert = object_name in {
        "ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE",
    }
    container_block_name = (
        str(layout_name).split(":", 1)[1]
        if str(layout_name).upper().startswith("BLOCKDEF:") else ""
    )
    nesting_context = "block_definition" if container_block_name else "drawing_space"
    if not is_insert:
        return {
            "insertion_point_wcs": None,
            "insertion_point_status": "not_applicable",
            "block_base_point": None,
            "block_base_point_status": "not_applicable",
            "insert_normal": None,
            "insert_normal_status": "not_applicable",
            "insert_extrusion": None,
            "insert_extrusion_status": "not_applicable",
            "insert_scale": None,
            "insert_scale_status": "not_applicable",
            "insert_rotation": None,
            "insert_rotation_status": "not_applicable",
            "container_block_name": container_block_name,
            "nesting_context": nesting_context,
            "block_definition_handle": "",
            "block_flags": None,
            "external_reference_path": "",
            "external_reference_status": "not_external",
        }, []

    reasons = []
    insertion, insertion_status, _, insertion_observed = _com_vector_alias(
        entity, ("InsertionPoint",),
    )
    block_base, block_base_status, _, base_observed = _com_vector_alias(
        entity, ("BlockBasePoint", "BlockOrigin", "DefinitionBasePoint"),
    )
    normal, normal_status, _, normal_observed = _com_vector_alias(
        entity, ("Normal",),
    )
    extrusion, extrusion_status, _, extrusion_observed = _com_vector_alias(
        entity, ("ExtrusionDirection",),
    )
    for field_name, status, observed in (
        ("insertion_point", insertion_status, insertion_observed),
        ("block_base_point", block_base_status, base_observed),
        ("insert_normal", normal_status, normal_observed),
        ("insert_extrusion", extrusion_status, extrusion_observed),
    ):
        if status != "available":
            detail = ",".join(f"{name}:{state}" for name, state in observed)
            reasons.append(f"{field_name}_unavailable_in_com_backend[{detail}]")

    scale_values = []
    scale_failures = []
    for field_name, property_name in (
        ("x", "XScaleFactor"), ("y", "YScaleFactor"), ("z", "ZScaleFactor"),
    ):
        value, status, detail = _com_finite_number_property(entity, property_name)
        if status != "available":
            scale_failures.append(f"{field_name}:{detail}")
        scale_values.append(value)
    if scale_failures:
        insert_scale = None
        insert_scale_status = "unavailable"
        reasons.append(
            "insert_scale_unavailable_in_com_backend[" + ",".join(scale_failures) + "]"
        )
    else:
        insert_scale = tuple(scale_values)
        insert_scale_status = "available"
        if any(value == 0.0 for value in insert_scale):
            reasons.append("zero_insert_scale")

    insert_rotation, insert_rotation_status, rotation_detail = (
        _com_finite_number_property(entity, "Rotation")
    )
    if insert_rotation_status != "available":
        reasons.append(
            f"insert_rotation_unavailable_in_com_backend[{rotation_detail}]"
        )

    block_definition_handle, _, _, _ = _com_text_alias(
        entity, ("BlockDefinitionHandle", "BlockTableRecordHandle"),
    )
    block_flags_value, block_flags_status = _com_property_status(entity, "BlockFlags")
    block_flags = None
    if block_flags_status == "available":
        try:
            block_flags = int(str(block_flags_value), 10)
        except (TypeError, ValueError):
            reasons.append("block_flags_invalid_in_com_backend")

    external_path, external_path_status, _, _ = _com_text_alias(
        entity, ("XRefPath", "Path"),
    )
    is_xref, is_xref_status = _com_property_status(entity, "IsXRef")
    is_overlay, is_overlay_status = _com_property_status(entity, "IsXRefOverlay")
    if is_overlay_status == "available" and bool(is_overlay):
        external_status = "xref_overlay"
    elif is_xref_status == "available" and bool(is_xref):
        external_status = "xref"
    elif external_path_status == "available":
        external_status = "external_reference"
    elif is_xref_status == "available" and not bool(is_xref):
        external_status = "not_external"
    else:
        external_status = "unknown"
        reasons.append("external_reference_status_unavailable_in_com_backend")

    return {
        "insertion_point_wcs": insertion,
        "insertion_point_status": insertion_status,
        "block_base_point": block_base,
        "block_base_point_status": block_base_status,
        "insert_normal": normal,
        "insert_normal_status": normal_status,
        "insert_extrusion": extrusion,
        "insert_extrusion_status": extrusion_status,
        "insert_scale": insert_scale,
        "insert_scale_status": insert_scale_status,
        "insert_rotation": insert_rotation,
        "insert_rotation_status": insert_rotation_status,
        "container_block_name": container_block_name,
        "nesting_context": nesting_context,
        "block_definition_handle": block_definition_handle,
        "block_flags": block_flags,
        "external_reference_path": external_path,
        "external_reference_status": external_status,
    }, reasons


def _native_length(entity):
    for property_name in ("Length", "ArcLength", "Circumference"):
        value = _float_or_none(_safe_get(entity, property_name))
        if value is not None and value >= 0:
            return value, f"autocad_com:{property_name}"
    return None, ""


def _entity_points(entity, object_name):
    if object_name == "ACDBLINE":
        return [point for point in (_xy(_safe_get(entity, "StartPoint")), _xy(_safe_get(entity, "EndPoint"))) if point]
    if object_name in {"ACDB2DPOLYLINE", "ACDB3DPOLYLINE"}:
        return _flat_points(_safe_get(entity, "Coordinates", ()), 3)
    if object_name in {"ACDBPOLYLINE", "ACDBLWPOLYLINE", "ACDBLEADER"}:
        return _flat_points(_safe_get(entity, "Coordinates", ()), 2)
    if object_name in {"ACDBPOINT", "ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE", "ACDBTABLE"}:
        point = _xy(_safe_get(entity, "InsertionPoint", _safe_get(entity, "Coordinates")))
        return [point] if point else []
    if object_name in _TEXT_OBJECTS:
        if object_name in {"ACDBMLEADER", "ACDBMULTILEADER"}:
            point = _xy(_safe_get(entity, "TextLocation", _safe_get(entity, "InsertionPoint")))
            if point is None:
                points = _flat_points(_safe_get(entity, "Coordinates", ()), 3)
                point = points[0] if points else None
            return [point] if point else []
        alignment = int(_safe_get(entity, "Alignment", 0) or 0)
        aligned = _xy(_safe_get(entity, "TextAlignmentPoint")) if alignment else None
        point = aligned or _xy(_safe_get(entity, "InsertionPoint", _safe_get(entity, "TextAlignmentPoint")))
        return [point] if point else []
    if "DIMENSION" in object_name:
        first = _xy(_safe_get(entity, "XLine1Point", _safe_get(entity, "ExtLine1Point")))
        second = _xy(_safe_get(entity, "XLine2Point", _safe_get(entity, "ExtLine2Point")))
        return [point for point in (first, second) if point is not None]
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
    reader_backend_status="com_direct",
    block_definition_record=None,
):
    """Convert one AutoCAD COM entity into a deterministic neutral record."""
    object_name = str(object_name_hint or _safe_get(entity, "ObjectName", "")).upper()
    layer_value = str(
        layer_hint if layer_hint is not None else _safe_get(entity, "Layer", "0")
    )
    strict_curve = (
        object_name in _COM_ROUTE_CURVE_OBJECTS
        and _com_layer_is_cable(layer_value)
    )
    points = _entity_points(entity, object_name)
    text, raw_text, text_source = _entity_text_facts(entity, object_name)
    block_name = ""
    block_effective_name = ""
    block_reference_name = ""
    attributes = {}
    dynamic_properties = {}
    dynamic_status = "not_applicable"
    unsupported_reasons = []
    instance_facts, instance_unsupported = _com_instance_facts(
        entity, object_name, layout_name,
    )
    if (
        object_name in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}
        and isinstance(block_definition_record, dict)
    ):
        if (
            instance_facts["block_base_point"] is None
            and block_definition_record.get("block_base_point") is not None
        ):
            instance_facts["block_base_point"] = tuple(
                block_definition_record["block_base_point"]
            )
            instance_facts["block_base_point_status"] = "available"
            instance_unsupported = [
                reason for reason in instance_unsupported
                if not reason.startswith("block_base_point_unavailable_in_com_backend")
            ]
        for key in ("block_definition_handle", "block_flags"):
            if instance_facts.get(key) in {None, ""}:
                instance_facts[key] = block_definition_record.get(key)
        if instance_facts["external_reference_status"] == "unknown":
            instance_facts["external_reference_status"] = str(
                block_definition_record.get("external_reference_status", "unknown")
            )
            instance_facts["external_reference_path"] = str(
                block_definition_record.get("external_reference_path", "")
            )
            if instance_facts["external_reference_status"] != "unknown":
                instance_unsupported = [
                    reason for reason in instance_unsupported
                    if reason != "external_reference_status_unavailable_in_com_backend"
                ]
    unsupported_reasons.extend(instance_unsupported)
    if object_name in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}:
        block_effective_name = str(_safe_get(entity, "EffectiveName", "") or "").strip()
        block_reference_name = str(_safe_get(entity, "Name", "") or "").strip()
        block_name = str(
            block_name_hint or block_effective_name or block_reference_name
        ).strip()
        attributes, attribute_unsupported = _block_attribute_facts(entity)
        unsupported_reasons.extend(attribute_unsupported)
        dynamic_properties, dynamic_status, dynamic_unsupported = _dynamic_block_facts(entity)
        unsupported_reasons.extend(dynamic_unsupported)
        if attributes:
            text = "\n".join(f"{key}={value}" for key, value in sorted(attributes.items()))
            raw_text = text
            text_source = "block_attributes"
    elif object_name in {
        "ACDBATTRIBUTE", "ACDBATTRIBUTEREFERENCE", "ACDBATTRIBUTEDEFINITION",
    }:
        tag = str(_safe_get(entity, "TagString", "")).strip().upper()
        if tag:
            attributes[tag] = text
    if object_name in {"ACDBMLEADER", "ACDBMULTILEADER"} and not text:
        unsupported_reasons.append("multileader_text_unavailable_in_com_backend")
    if object_name == "ACDBTABLE" and not text:
        unsupported_reasons.append("table_text_unavailable_in_com_backend")
    is_dimension = "DIMENSION" in object_name
    dimension_value = _float_or_none(_safe_get(entity, "Measurement")) if is_dimension else None
    dimension_text_override = str(_safe_get(entity, "TextOverride", "") or "") if is_dimension else ""
    if is_dimension and dimension_value is None:
        unsupported_reasons.append("dimension_measurement_unavailable_in_com_backend")
    if is_dimension and len(points) < 2:
        unsupported_reasons.append("dimension_definition_points_unavailable_in_com_backend")
    # Every database object survives the all-object inventory boundary.  An
    # unsupported HATCH/proxy/etc. is evidence with empty geometry, never a
    # silently dropped row or a synthetic point at the origin.
    is_supported_object = (
        object_name in _COM_SUPPORTED_OBJECTS or is_dimension
    )
    external_reference_status = instance_facts["external_reference_status"]
    if not is_supported_object:
        unsupported_reasons.append("geometry_unsupported_in_com_backend")
    if external_reference_status not in {"not_external", "unknown"}:
        unsupported_reasons.append("external_reference_geometry_not_embedded")
    centroid = (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    ) if points else (0.0, 0.0)
    record_scale = instance_facts["insert_scale"] or (1.0, 1.0, 1.0)
    scale_x, scale_y, scale_z = record_scale
    native_length, native_length_source = _native_length(entity)
    closed = bool(_safe_get(entity, "Closed", object_name == "ACDBCIRCLE"))
    record = {
        "handle": str(_safe_get(entity, "Handle", "")),
        "object_name": object_name,
        "dwg_type_name": (
            "DIMENSION"
            if is_dimension
            else "POLYLINE"
            if object_name in {"ACDBPOLYLINE", "ACDB2DPOLYLINE", "ACDB3DPOLYLINE"}
            else object_name.removeprefix("ACDB").upper()
        ),
        "layout": layout_name,
        "layout_role": layout_role,
        "cad_role": layout_role,
        "layer": layer_value,
        "points": points,
        "centroid": centroid,
        "closed": closed,
        "text": text,
        "block_name": block_name,
        "block_attributes": attributes,
        "aci_color": int(_safe_get(entity, "Color", 256) or 256),
        "true_color": _true_color(entity),
        "linetype": str(_safe_get(entity, "Linetype", "ByLayer")),
        "lineweight": int(_safe_get(entity, "Lineweight", -1) or -1),
        "rotation": (
            instance_facts["insert_rotation"]
            if instance_facts["insert_rotation"] is not None
            else 0.0
        ),
        "scale_x": scale_x,
        "scale_y": scale_y,
        "scale_z": scale_z,
        "dimension_value": dimension_value,
        "dimension_text_override": dimension_text_override,
        "owner_handle": str(
            _safe_get(entity, "OwnerHandle", _safe_get(entity, "OwnerID", "")) or ""
        ),
        "insertion_point": instance_facts["insertion_point_wcs"],
        "insertion_point_wcs": instance_facts["insertion_point_wcs"],
        "insertion_point_status": instance_facts["insertion_point_status"],
        "block_base_point": instance_facts["block_base_point"],
        "block_base_point_status": instance_facts["block_base_point_status"],
        "insert_normal": instance_facts["insert_normal"],
        "normal": instance_facts["insert_normal"],
        "insert_normal_status": instance_facts["insert_normal_status"],
        "insert_extrusion": instance_facts["insert_extrusion"],
        "extrusion": instance_facts["insert_extrusion"],
        "insert_extrusion_status": instance_facts["insert_extrusion_status"],
        "container_block_name": instance_facts["container_block_name"],
        "nesting_context": instance_facts["nesting_context"],
        "block_definition_handle": instance_facts["block_definition_handle"],
        "block_flags": instance_facts["block_flags"],
        "external_reference_path": instance_facts["external_reference_path"],
        "external_reference_status": external_reference_status,
        "geometry_status": (
            "anchor_only"
            if object_name in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}
            and instance_facts["insertion_point_wcs"] is not None
            else "available" if points else "unavailable"
        ),
        "inventory_support_status": (
            "supported"
            if is_supported_object and external_reference_status == "not_external"
            else "inventory_only"
        ),
        "scale": instance_facts["insert_scale"],
        "native_length": native_length,
    }
    curve_facts, curve_fingerprint = _com_curve_facts(
        entity, object_name, closed, native_length, native_length_source,
        strict=strict_curve, unsupported_reasons=unsupported_reasons,
    )
    record["curve_facts"] = curve_facts
    record["curve_fingerprint"] = curve_fingerprint
    if object_name in _COM_CURVE_OBJECTS:
        record["curve_facts_status"] = (
            "available" if curve_facts else
            "unreadable" if any(str(reason).startswith("curve_") for reason in unsupported_reasons)
            else "not_applicable"
        )
    record["raw_properties"] = _canonical_raw_properties(
        record,
        extraction_backend="autocad_com",
        reader_backend_status=reader_backend_status,
        owner_handle=record["owner_handle"],
        raw_text=raw_text,
        text_source=text_source,
        dimension_text_override=dimension_text_override,
        dynamic_block_properties=dynamic_properties,
        dynamic_block_properties_status=dynamic_status,
        block_effective_name=block_effective_name,
        block_reference_name=block_reference_name,
        native_length_source=native_length_source,
        curve_facts=curve_facts,
        curve_fingerprint=curve_fingerprint,
        insertion_point_wcs=record["insertion_point_wcs"],
        insertion_point_status=record["insertion_point_status"],
        block_base_point=record["block_base_point"],
        block_base_point_status=record["block_base_point_status"],
        insert_normal=record["insert_normal"],
        insert_normal_status=record["insert_normal_status"],
        insert_extrusion=record["insert_extrusion"],
        insert_extrusion_status=record["insert_extrusion_status"],
        insert_scale=instance_facts["insert_scale"],
        insert_scale_status=instance_facts["insert_scale_status"],
        insert_rotation=instance_facts["insert_rotation"],
        insert_rotation_status=instance_facts["insert_rotation_status"],
        container_block_name=record["container_block_name"],
        nesting_context=record["nesting_context"],
        block_definition_handle=record["block_definition_handle"],
        block_flags=record["block_flags"],
        external_reference_path=record["external_reference_path"],
        external_reference_status=record["external_reference_status"],
        geometry_status=record["geometry_status"],
        inventory_support_status=record["inventory_support_status"],
        unsupported_reasons=unsupported_reasons,
    )
    return record


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


def _extract_com_block_record(block, block_name, reader_backend_status):
    """Materialize one block-table record, including unloaded XREF evidence."""
    layout_name = f"BLOCKDEF:{block_name}"
    base_point, base_status, _, base_observed = _com_vector_alias(
        block, ("Origin", "BasePoint"),
    )
    path, path_status, _, _ = _com_text_alias(block, ("Path", "XRefPath"))
    is_xref, is_xref_status = _com_property_status(block, "IsXRef")
    is_overlay, is_overlay_status = _com_property_status(block, "IsXRefOverlay")
    if is_overlay_status == "available" and bool(is_overlay):
        external_status = "xref_overlay"
    elif is_xref_status == "available" and bool(is_xref):
        external_status = "xref"
    elif path_status == "available":
        external_status = "external_reference"
    elif is_xref_status == "available" and not bool(is_xref):
        external_status = "not_external"
    else:
        external_status = "unknown"
    flags_value, flags_status = _com_property_status(block, "BlockFlags")
    block_flags = None
    if flags_status == "available":
        try:
            block_flags = int(str(flags_value), 10)
        except (TypeError, ValueError):
            block_flags = None
    reasons = ["geometry_unavailable_for_block_definition_record"]
    if base_status != "available":
        detail = ",".join(f"{name}:{status}" for name, status in base_observed)
        reasons.append(f"block_base_point_unavailable_in_com_backend[{detail}]")
    if external_status not in {"not_external", "unknown"}:
        reasons.append("external_reference_geometry_not_embedded")
    record = {
        "handle": str(_safe_get(block, "Handle", "") or ""),
        "object_name": "ACDBBLOCKTABLERECORD",
        "dwg_type_name": "BLOCK_RECORD",
        "layout": layout_name,
        "layout_role": "block_definition",
        "cad_role": "block_definition",
        "layer": "0",
        "points": [],
        "centroid": (0.0, 0.0),
        "closed": False,
        "text": "",
        "block_name": str(block_name),
        "block_attributes": {},
        "aci_color": 256,
        "true_color": "",
        "linetype": "ByLayer",
        "lineweight": -1,
        "rotation": 0.0,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "scale_z": 1.0,
        "dimension_value": None,
        "dimension_text_override": "",
        "owner_handle": str(
            _safe_get(block, "OwnerHandle", _safe_get(block, "OwnerID", "")) or ""
        ),
        "native_length": None,
        "curve_facts": {},
        "curve_fingerprint": "",
        "insertion_point": None,
        "insertion_point_wcs": None,
        "insertion_point_status": "not_applicable",
        "block_base_point": base_point,
        "block_base_point_status": base_status,
        "insert_normal": None,
        "normal": None,
        "insert_normal_status": "not_applicable",
        "insert_extrusion": None,
        "extrusion": None,
        "insert_extrusion_status": "not_applicable",
        "container_block_name": "",
        "nesting_context": "block_definition_record",
        "block_definition_handle": str(_safe_get(block, "Handle", "") or ""),
        "block_flags": block_flags,
        "external_reference_path": path,
        "external_reference_status": external_status,
        "geometry_status": "unavailable",
        "inventory_support_status": "inventory_only",
        "scale": None,
    }
    record["raw_properties"] = _canonical_raw_properties(
        record,
        extraction_backend="autocad_com",
        reader_backend_status=reader_backend_status,
        owner_handle=record["owner_handle"],
        block_effective_name=record["block_name"],
        block_base_point=base_point,
        block_base_point_status=base_status,
        nesting_context="block_definition_record",
        block_definition_handle=record["block_definition_handle"],
        block_flags=block_flags,
        external_reference_path=path,
        external_reference_status=external_status,
        geometry_status="unavailable",
        inventory_support_status="inventory_only",
        unsupported_reasons=reasons,
    )
    return record


def _collect_records(database, assign_fc=None, reader_backend_status="com_direct"):
    collections = [("Model", "model", database.ModelSpace)]
    block_definitions = {}
    block_definition_records = {}
    try:
        layouts = _safe_get(database, "Layouts")
        for layout in _iter_com_collection(layouts):
            name = str(_safe_get(layout, "Name", ""))
            if name.casefold() == "model":
                continue
            collections.append((name, classify_layout_role(name), _safe_get(layout, "Block")))
    except Exception:
        pass
    try:
        blocks = _safe_get(database, "Blocks")
        for block in _iter_com_collection(blocks):
            name = str(_safe_get(block, "Name", ""))
            if not name or name.upper() == "*MODEL_SPACE" or name.upper().startswith("*PAPER_SPACE"):
                continue
            if bool(_safe_get(block, "IsLayout", False)):
                continue
            layout_name = f"BLOCKDEF:{name}"
            collections.append((layout_name, "block_definition", block))
            block_definitions[layout_name] = block
            block_definition_records[name.upper()] = _extract_com_block_record(
                block, name, reader_backend_status,
            )
    except Exception:
        # COM is an explicitly non-equivalent fallback.  The absence of the
        # Blocks collection is observable through missing block records; no
        # base point is synthesized downstream.
        pass
    grouped = []
    for layout_name, layout_role, collection in collections:
        records = []
        if collection is None:
            continue
        if layout_name in block_definitions:
            records.append(block_definition_records[
                layout_name.split(":", 1)[1].upper()
            ])
        # The inventory boundary is semantic-free: it must enumerate every
        # object even when the legacy caller supplied a classifier.
        entity_collections = [collection]
        seen_handles = set()
        for entity_collection in entity_collections:
          for entity in _iter_com_collection(entity_collection):
            object_name = str(_safe_get(entity, "ObjectName", "")).upper()
            layer_name = str(_safe_get(entity, "Layer", "0"))
            block_name = ""
            if object_name in {"ACDBBLOCKREFERENCE", "ACDBDYNAMICBLOCKREFERENCE"}:
                block_name = str(
                    _safe_get(entity, "EffectiveName", _safe_get(entity, "Name", ""))
                )
            handle = str(_safe_get(entity, "Handle", ""))
            if handle and handle in seen_handles:
                continue
            seen_handles.add(handle)
            record = extract_com_entity(
                entity, layout_name, layout_role,
                object_name_hint=object_name,
                layer_hint=layer_name,
                block_name_hint=block_name,
                reader_backend_status=reader_backend_status,
                block_definition_record=block_definition_records.get(
                    block_name.upper()
                ),
            )
            if record is not None:
                records.append(record)
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


def read_dwg_with_autocad(
    dwg_path,
    reproject_point,
    assign_fc,
    classify_block,
    extract_attributes,
    *,
    accoreconsole=None,
    timeout=None,
    compatibility_policy=BULK_POLICY_STRICT,
):
    """Read a DWG directly through AutoCAD; never export or create a DXF."""
    if os.name != "nt":
        raise RuntimeError("Direct AutoCAD DWG reading requires Windows")
    source = Path(dwg_path).resolve()
    if source.suffix.casefold() != ".dwg":
        raise ValueError("The direct AutoCAD reader accepts DWG input only")
    try:
        grouped = _extract_records_with_core_console(
            source,
            accoreconsole=accoreconsole,
            timeout=timeout,
            compatibility_policy=compatibility_policy,
        )
        items = _items_from_grouped(
            grouped, source, reproject_point, assign_fc, classify_block, extract_attributes,
        )
        return _bind_entity_keys(items, source)
    except Exception as bulk_error:
        _authorize_com_fallback(bulk_error)

    pythoncom, application, created, database, opened_document = _open_autocad_database(source)
    try:
        grouped = _collect_records(
            database,
            assign_fc=assign_fc,
            reader_backend_status="fallback_after_core_console_failure",
        )
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


def extract_dwg_records(
    dwg_path,
    *,
    accoreconsole=None,
    timeout=None,
    compatibility_policy=BULK_POLICY_STRICT,
):
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
    protocol_diagnostics = {}
    try:
        grouped = _extract_records_with_core_console(
            source,
            accoreconsole=accoreconsole,
            timeout=timeout,
            compatibility_policy=compatibility_policy,
        )
        protocol_diagnostics = dict(getattr(grouped, "diagnostics", {}) or {})
        protocol_diagnostics["backend"] = "autocad_core_console_bulk"
    except Exception as bulk_error:
        _authorize_com_fallback(bulk_error)
        pythoncom, application, created, database, opened_document = _open_autocad_database(source)
        try:
            grouped = _collect_records(
                database,
                assign_fc=None,
                reader_backend_status="fallback_after_core_console_failure",
            )
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
        protocol_diagnostics = {
            "backend": "autocad_com",
            "compatibility_policy": _validate_bulk_compatibility_policy(
                compatibility_policy
            ),
            "total_rows": sum(len(items) for _, _, items in grouped),
            "parsed_rows": sum(len(items) for _, _, items in grouped),
            "skipped_rows": 0,
            "skipped_row_errors": [],
            "core_console_error": str(bulk_error),
        }

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
    protocol_diagnostics["returned_records"] = len(records)
    protocol_diagnostics["inventory_complete"] = (
        protocol_diagnostics.get("skipped_rows", 0) == 0
    )
    return DWGRecordInventory(records, diagnostics=protocol_diagnostics)
