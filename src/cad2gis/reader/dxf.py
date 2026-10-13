"""Native DXF ingestion through the shared, source-bound entity projection."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .contracts import DWGRecordInventory
from .libredwg_cli import records_from_dxf_document


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def extract_dxf_records(source_path: str | Path, *,
                        layout_filter: str | None = None) -> DWGRecordInventory:
    import ezdxf
    source = Path(source_path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".dxf":
        raise ValueError(f"Expected an existing DXF source: {source}")
    source_hash = _sha256(source)
    document = ezdxf.readfile(source)
    records = records_from_dxf_document(document, source, source_hash,
        layout_filter=layout_filter if layout_filter is not None else os.environ.get("CAD2GIS_LAYOUT") or None,
        backend="native_dxf")
    if _sha256(source) != source_hash:
        raise ValueError("DXF source changed while its native inventory was being read")
    records.diagnostics.update(source_format="DXF", source_sha256=source_hash,
                               intermediate_persisted=False,
                               geodata={"status": "dxf_header_only"})
    return records
