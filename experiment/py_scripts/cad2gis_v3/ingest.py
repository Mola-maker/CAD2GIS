"""Immutable direct-DWG ingestion and census validation."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from autocad_reader import extract_dwg_records

from .config import SourceProfile
from .model import SourceEntity


def ingest(source: str | Path, profile: SourceProfile) -> tuple[list[SourceEntity], dict]:
    source_path = Path(source).resolve()
    source_hash = profile.validate_source(source_path)
    records = extract_dwg_records(source_path)
    entities = [SourceEntity.from_record(record) for record in records]
    model = [entity for entity in entities if entity.layout.casefold() == "model"]
    metadata = next((entity.text for entity in entities if entity.dwg_type == "DOCUMENT_METADATA"), "")
    expected_cgeocs = f"CGEOCS={profile.dwg_cgeocs}"
    if expected_cgeocs.casefold() not in metadata.casefold():
        raise ValueError(f"DWG CRS evidence mismatch: expected {expected_cgeocs}, got {metadata!r}")
    expected_insunits = f"INSUNITS={profile.dwg_insunits}"
    if expected_insunits.casefold() not in metadata.casefold():
        raise ValueError(f"DWG unit evidence mismatch: expected {expected_insunits}, got {metadata!r}")
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
            "unsupported_reasons": dict(sorted(unsupported_reasons.items())),
        },
    }
    return entities, diagnostics
