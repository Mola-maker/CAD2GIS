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
    diagnostics = {
        "source_path": str(source_path),
        "source_sha256": source_hash,
        "dwg_metadata": metadata,
        "drawing_units": {"insunits": profile.dwg_insunits, "name": profile.drawing_units},
        "census": census,
        "layouts": dict(sorted(Counter(entity.layout for entity in entities).items())),
        "roles": dict(sorted(Counter(entity.cad_role for entity in entities).items())),
    }
    return entities, diagnostics
