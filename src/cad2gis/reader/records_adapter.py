"""Source-bound records-bundle adapter for deterministic replay.

The pipeline's canonical entry point is ``ingest(source_path, profile)`` which
expects a real DWG path. For reader-independent verification, this adapter:

1. accepts an explicit bundle path;
2. verifies the bundle schema and source hash binding;
3. materializes each canonical fact record as a ``SourceEntity``;
4. feeds those immutable facts into the deterministic conversion stages.

No project name, filename, entity count, layer, coordinate, or expected output
is selected here. Source-specific expectations belong only in reviewed project
profiles and test fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..cad2gis_v3.config import SourceProfile
from ..cad2gis_v3.model import SourceEntity

_MAX_BUNDLE_BYTES = 256 * 1024 * 1024


def _load_bundle(bundle_path: Path) -> dict:
    if bundle_path.stat().st_size > _MAX_BUNDLE_BYTES:
        raise ValueError(
            f"records bundle exceeds maximum allowed size ({_MAX_BUNDLE_BYTES} bytes)"
        )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if not isinstance(bundle, dict) or not isinstance(bundle.get("objects"), list):
        raise ValueError("invalid records bundle schema")
    return bundle


def load_records(bundle_path: Path) -> list[SourceEntity]:
    """Materialise a records bundle into SourceEntity list."""
    bundle = _load_bundle(bundle_path)
    return [
        SourceEntity.from_record(obj["facts"])
        for obj in bundle["objects"]
    ]


def validate_bundle_facts(bundle_path: Path, profile: SourceProfile) -> dict:
    """Verify bundle schema invariants + profile binding."""
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
    }
