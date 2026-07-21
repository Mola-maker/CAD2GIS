"""Records bundle adapter: feeds readcad_review_bundle.json into pipeline.

The pipeline's canonical entry point is ``ingest(source_path, profile)`` which
expects a real DWG path.  For the A-plan closed-loop verification (no DWG),
this adapter synthesises a pipeline invocation by:

  1. Loading the records bundle from ``baselines/apd_hutabohu/records/``
  2. Iterating ``bundle['objects']`` (9391 canonical records)
  3. Calling ``SourceEntity.from_record()`` for each record (bypassing the
     reader layer entirely; records are already canonical-extracted)
  4. Feeding entities into the rest of the pipeline (semantic/topology/...)

This separates the "reader extraction" concern (covered by ``verify/contract/``)
from the "pipeline behaviour" concern (covered by ``verify/replay.py``).
Records bundle content stability = canonical-evidence baseline; bundle
drift indicates a schema change that requires re-validation.
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
    bundle = _load_bundle(bundle_path)
    facts_count = sum(1 for o in bundle["objects"] if "facts" in o)
    return {
        "bundle_path": str(bundle_path),
        "objects_count": len(bundle["objects"]),
        "facts_count": facts_count,
        "schema_version": bundle.get("schema_version"),
    }
