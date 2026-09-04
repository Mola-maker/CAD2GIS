"""Compatibility API for review-bundle integrity and source binding.

Current curation bundles are proposal-only: they omit native coordinates and
do not contain complete ``SourceEntity`` records. Integrity validation does
not authorize conversion or establish replay readiness. ``load_records`` is
retained as an explicitly unsupported entry point until a complete source
record replay protocol exists. Conversion uses the canonical DWG entry point,
``cad2gis.cad2gis_v3.ingest.ingest(source, profile)``.
"""

from __future__ import annotations

from pathlib import Path

from ..cad2gis_v3.config import SourceProfile
from ..cad2gis_v3.model import SourceEntity

def load_records(bundle_path: Path) -> list[SourceEntity]:
    """Retain the legacy signature without materializing incomplete facts."""
    raise NotImplementedError(
        "Record-bundle replay is not implemented. Current review bundles are "
        "proposal-only and cannot be materialized as SourceEntity records. "
        "Use cad2gis.cad2gis_v3.ingest.ingest(source, profile) with the "
        "original DWG for canonical conversion."
    )


def validate_bundle_facts(bundle_path: Path, profile: SourceProfile) -> dict:
    """Verify review-bundle integrity and profile binding, not replay readiness.

    A successful result still has ``conversion_import_allowed=False``.
    """
    from ..cad2gis_v3.curation import load_review_bundle

    review_bundle = load_review_bundle(bundle_path)
    bundle = review_bundle.payload
    if review_bundle.source_sha256 != profile.source_sha256:
        raise ValueError(
            "records bundle source SHA-256 does not match the source profile"
        )
    facts_count = sum(
        isinstance(item, dict) and isinstance(item.get("facts"), dict)
        for item in bundle["objects"]
    )
    if facts_count != len(bundle["objects"]):
        raise ValueError("records bundle contains objects without fact mappings")
    return {
        "bundle_path": str(bundle_path),
        "objects_count": len(bundle["objects"]),
        "facts_count": facts_count,
        "schema_version": bundle["schema_version"],
        "bundle_sha256": review_bundle.bundle_sha256,
        "source_sha256": review_bundle.source_sha256,
        "conversion_import_allowed": False,
    }
