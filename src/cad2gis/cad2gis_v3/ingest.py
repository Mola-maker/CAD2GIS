"""Immutable direct-DWG ingestion and census validation."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Callable

from ..reader.contracts import DWGRecordInventory
from .config import SourceProfile
from .model import SourceEntity

_READER_ENV = "CAD2GIS_READER_BACKEND"
_DEFAULT_READER = "libredwg"


def _default_extract_records(source_path: Path) -> DWGRecordInventory:
    backend = os.environ.get(_READER_ENV, _DEFAULT_READER).strip().lower()
    if backend == "libredwg":
        from ..reader.libredwg import extract_dwg_records
    elif backend == "autocad":
        from ..reader.autocad import extract_dwg_records
    else:
        raise ValueError(
            f"unknown reader backend {backend!r}; expected libredwg or autocad"
        )
    return extract_dwg_records(source_path)


def ingest(
    source: str | Path,
    profile: SourceProfile,
    *,
    extract_records: Callable[[Path], DWGRecordInventory] | None = None,
) -> tuple[list[SourceEntity], dict]:
    source_path = Path(source).resolve()
    source_hash = profile.validate_source(source_path)
    if extract_records is None:
        extract_records = _default_extract_records
    records = extract_records(source_path)
    reader_protocol = dict(getattr(records, "diagnostics", {}) or {})
    if (
        int(reader_protocol.get("skipped_rows", 0) or 0) != 0
        or reader_protocol.get("inventory_complete") is False
    ):
        raise RuntimeError(
            "Authoritative reader inventory is incomplete; compatibility-mode "
            f"skips cannot enter conversion: {reader_protocol}"
        )
    entities = [SourceEntity.from_record(record) for record in records]
    model = [entity for entity in entities if entity.layout.casefold() == "model"]
    metadata = next((entity.text for entity in entities if entity.dwg_type == "DOCUMENT_METADATA"), "")
    if profile.dwg_cgeocs is not None:
        expected_cgeocs = f"CGEOCS={profile.dwg_cgeocs}"
        if expected_cgeocs.casefold() not in metadata.casefold():
            raise ValueError(
                f"DWG CRS evidence mismatch: expected {expected_cgeocs}, got {metadata!r}"
            )
    if profile.dwg_insunits is not None:
        expected_insunits = f"INSUNITS={profile.dwg_insunits}"
        if expected_insunits.casefold() not in metadata.casefold():
            raise ValueError(
                f"DWG unit evidence mismatch: expected {expected_insunits}, got {metadata!r}"
            )
    census = {
        "model_entities": len(model),
        "model_inserts": sum(entity.dwg_type == "INSERT" for entity in model),
        "model_dimensions": sum(entity.dwg_type == "DIMENSION" for entity in model),
    }
    for key in ("model_entities", "model_inserts", "model_dimensions"):
        expected = profile.expected_census.get(key)
        if expected is not None and census[key] != expected:
            raise ValueError(f"Authoritative census mismatch for {key}: expected {expected}, got {census[key]}")

    annotation_carriers = {
        "TEXT", "MTEXT", "ATTRIB", "ATTDEF", "MLEADER", "MULTILEADER",
        "TABLE", "TABLE_CELL",
    }
    carrier_counts = Counter(
        entity.dwg_type for entity in entities if entity.dwg_type in annotation_carriers
    )
    carrier_text_counts = Counter(
        entity.dwg_type
        for entity in entities
        if entity.dwg_type in annotation_carriers and entity.text.strip()
    )
    extraction_backends = Counter(
        entity.extraction_backend or "UNAVAILABLE" for entity in entities
    )
    reader_backend_statuses = Counter(
        entity.reader_backend_status or "UNAVAILABLE" for entity in entities
    )
    curve_entities = [entity for entity in entities if entity.curve_facts]
    curve_schema_versions = Counter(
        entity.curve_schema_version or "UNAVAILABLE" for entity in curve_entities
    )
    curve_primitive_types = Counter(
        str(entity.curve_facts.get("primitive_type", "UNAVAILABLE"))
        for entity in curve_entities
    )
    unsupported_reasons = Counter()
    dynamic_block_statuses = Counter()
    for entity in entities:
        for reason in entity.raw_properties.get("unsupported_reasons", ()) or ():
            unsupported_reasons[str(reason)] += 1
        if entity.dwg_type == "INSERT":
            dynamic_block_statuses[
                str(entity.raw_properties.get("dynamic_block_properties_status", "UNAVAILABLE"))
            ] += 1

    diagnostics = {
        "source_path": str(source_path),
        "source_sha256": source_hash,
        "dwg_metadata": metadata,
        "drawing_units": {"insunits": profile.dwg_insunits, "name": profile.drawing_units},
        "census": census,
        "layouts": dict(sorted(Counter(entity.layout for entity in entities).items())),
        "roles": dict(sorted(Counter(entity.cad_role for entity in entities).items())),
        "reader_inventory": {
            "extraction_backends": dict(sorted(extraction_backends.items())),
            "backend_statuses": dict(sorted(reader_backend_statuses.items())),
            "raw_property_schema_versions": dict(sorted(Counter(
                str(entity.raw_properties.get("schema_version", "UNAVAILABLE"))
                for entity in entities
            ).items())),
            "annotation_carriers": dict(sorted(carrier_counts.items())),
            "annotation_carriers_with_text": dict(sorted(carrier_text_counts.items())),
            "block_instances": sum(entity.dwg_type == "INSERT" for entity in entities),
            "nested_block_instances": sum(
                entity.dwg_type == "INSERT" and entity.layout_role == "block_definition"
                for entity in entities
            ),
            "dynamic_block_property_statuses": dict(sorted(dynamic_block_statuses.items())),
            "native_length_entities": sum(entity.native_length is not None for entity in entities),
            "curve_facts_entities": len(curve_entities),
            "curve_fingerprint_entities": sum(
                bool(entity.curve_fingerprint) for entity in curve_entities
            ),
            "curve_facts_schema_versions": dict(sorted(curve_schema_versions.items())),
            "curve_primitive_types": dict(sorted(curve_primitive_types.items())),
            "curve_entities_with_nonzero_bulge": sum(
                any(abs(float(value)) > 0.0 for value in entity.curve_facts.get("bulges", ()))
                for entity in curve_entities
            ),
            "curve_entities_with_nonzero_elevation": sum(
                entity.curve_facts.get("elevation") is not None
                and abs(float(entity.curve_facts["elevation"])) > 0.0
                for entity in curve_entities
            ),
            "unsupported_reasons": dict(sorted(unsupported_reasons.items())),
        },
        "reader_protocol": reader_protocol,
    }
    return entities, diagnostics
