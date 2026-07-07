"""DWG/DXF ingest & normalization (story G3, ingest half).

Real organizer drawings are AutoCAD 2007 binary DWG (AC1021). ezdxf cannot read binary DWG,
and GDAL's OGR 'CAD' driver (libopencad 0.3.4) only supports DWG R2000 [ACAD1015] — verified
to FAIL on these AC1021 files. So DWG must be normalized to DXF by a real converter first.

Two-tier strategy (designed with the Codex reviewer, confirmed against the real dataset):
  Tier 1 (semantic DXF, preferred):
    - .dxf input            -> use as-is
    - ODA File Converter    -> R2007 DXF (via ezdxf.addons.odafc)      [best fidelity]
    - LibreDWG dwg2dxf      -> R2007 DXF                              [conda-installable, automatable]
  Tier 2 (geometry salvage): GDAL CAD -> GeoPackage (only if the DWG happens to be R2000).
  Else: raise with actionable install instructions.

`normalize_to_dxf()` returns a path to a parseable DXF (converting if needed). The parser
(parse_dxf) stays DXF-only; this module is the single place that knows about DWG.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class IngestResult:
    dxf_path: str
    method: str          # "passthrough" | "odafc" | "dwg2dxf"
    source: str
    converted: bool
    note: str = ""


def _dwg2dxf_exe() -> Optional[str]:
    exe = shutil.which("dwg2dxf")
    if exe:
        return exe
    # dwg2dxf (LibreDWG) is a standalone C tool; we install it in an isolated 'dwgtools' env
    # to avoid re-solving the big geo env. Also check the conda env Library\bin locations.
    home = os.path.expanduser("~")
    candidates = []
    for base in (
        os.environ.get("DWG2DXF_HOME"),
        os.path.join(home, "miniconda3", "envs", "dwgtools"),
        os.environ.get("CONDA_PREFIX"),
        os.path.join(home, "miniconda3", "envs", "cad2gis"),
        os.path.join(home, "miniconda3"),
    ):
        if base:
            candidates.append(os.path.join(base, "Library", "bin", "dwg2dxf.exe"))
            candidates.append(os.path.join(base, "bin", "dwg2dxf"))
    for cand in candidates:
        if os.path.isfile(cand):
            return cand
    return None


def _oda_available() -> bool:
    try:
        from ezdxf.addons import odafc

        return bool(odafc.is_installed())
    except Exception:  # noqa: BLE001
        return False


def normalize_to_dxf(path: str, out_dir: str = "build/normalized") -> IngestResult:
    """Return a parseable DXF path for *path*, converting DWG->DXF if necessary."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        return IngestResult(path, "passthrough", path, converted=False)
    if ext != ".dwg":
        raise ValueError(f"unsupported CAD extension: {ext} ({path})")

    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    out_dxf = os.path.join(out_dir, stem + ".dxf")

    # Tier 1a: ODA File Converter (best fidelity) via ezdxf odafc.
    if _oda_available():
        from ezdxf.addons import odafc

        odafc.convert(path, out_dxf, version="R2007", audit=True, replace=True)
        return IngestResult(out_dxf, "odafc", path, converted=True, note="ODA File Converter R2007")

    # Tier 1b: LibreDWG dwg2dxf (conda-installable, automatable).
    exe = _dwg2dxf_exe()
    if exe:
        proc = subprocess.run(
            [exe, "-y", "--as", "r2007", "-o", out_dxf, path],
            capture_output=True, text=True,
        )
        if proc.returncode == 0 and os.path.isfile(out_dxf) and os.path.getsize(out_dxf) > 0:
            return IngestResult(out_dxf, "dwg2dxf", path, converted=True, note="LibreDWG r2007")
        raise RuntimeError(
            f"dwg2dxf failed (rc={proc.returncode}) for {path}\nstderr: {proc.stderr[:500]}"
        )

    raise RuntimeError(
        "No DWG->DXF converter available. Install one of:\n"
        "  conda install -n cad2gis -c conda-forge libredwg   (provides dwg2dxf)\n"
        "  or install ODA File Converter and let ezdxf.addons.odafc find it."
    )
